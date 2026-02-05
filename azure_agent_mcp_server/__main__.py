"""
Azure AI Agent Service MCP Server

This server connects to Azure AI Agent Service and dynamically registers
agents as FastMCP tools that can be queried by clients.
"""

import os
import sys
import logging
import asyncio
import re
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from fastmcp import FastMCP, Context
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import AgentDetails
from azure.identity.aio import DefaultAzureCredential
from azure.core.exceptions import ServiceRequestError, HttpResponseError, ResourceNotFoundError
from openai import AsyncOpenAI

# Configure structured logging with timestamp, module, and log level
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("azure_agent_mcp")

# Global variables for client and agent cache
project_client: Optional[AIProjectClient] = None
openai_client: Optional[AsyncOpenAI] = None
registered_agents: Dict[str, Dict[str, Any]] = {}  # Dictionary to keep track of registered agent tools by name
server_initialized = False
server_type = None
server_port = None
server_path = None
update_interval = 300  # Default update interval in seconds

# Configuration constants
MAX_RETRIES = 2
BASE_BACKOFF_DELAY = 1  # Base delay for exponential backoff in seconds
MAX_POLL_DELAY = 5  # Maximum polling delay in seconds
DEFAULT_PORT = 8000
DEFAULT_PATH = "/"

def initialize_server() -> bool:
    """
    Initialize the Azure AI Project client and server configuration.
    
    Returns:
        bool: True if initialization succeeded, False otherwise.
    """
    global project_client, openai_client, server_type, server_port, server_path, update_interval

    # Load environment variables from .env file if present
    load_dotenv()
    
    # Load configuration from environment variables
    project_endpoint = os.getenv("PROJECT_ENDPOINT")
    if project_endpoint:
        project_endpoint = project_endpoint.strip()
    update_interval = int(os.getenv("UPDATE_INTERVAL", update_interval))

    # Configure server type and networking
    server_type = os.getenv("SERVER_TYPE", "local").lower()
    server_port = int(os.getenv("SERVER_PORT", DEFAULT_PORT))
    server_path = os.getenv("SERVER_PATH", DEFAULT_PATH)

    log_level = os.getenv("LOG_LEVEL", "WARNING").upper()
    valid_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}

    if log_level not in valid_levels:
        log_level = "WARNING"  # fallback or raise an error

    logger.setLevel(log_level)

    # Validate server type configuration
    if server_type not in ["local", "web"]:
        logger.error(f"Invalid server type: {server_type}. Must be 'local' or 'web'.")
        return False

    # Validate essential environment variables
    if not project_endpoint:
        logger.error("Missing required environment variable: PROJECT_ENDPOINT")
        return False

    try:
        # Initialize the Azure AI Project client with managed identity authentication
        project_client = AIProjectClient(
            credential=DefaultAzureCredential(),
            endpoint=project_endpoint
        )
        # Get the OpenAI client for invoking agents
        openai_client = project_client.get_openai_client()
        logger.info(f"Successfully initialized Azure AI Project client for endpoint: {project_endpoint}")
        return True
    
    except Exception as e:
        logger.error(f"Failed to initialize AIProjectClient: {str(e)}")
        return False


async def query_agent(agent_name: str, query: str) -> str:
    """
    Query an Azure AI Agent using the responses API with retry logic.
    
    Args:
        agent_name: The name of the agent to query
        query: The text query to send to the agent
        
    Returns:
        str: The formatted response from the agent
        
    Raises:
        Exception: If the query fails after all retry attempts
    """
    # Implement retry with exponential backoff
    for attempt in range(MAX_RETRIES):
        try:
            logger.debug(f"Invoking agent {agent_name} with query: {query[:100]}...")
            
            # Use the responses API with agent reference
            response = await openai_client.responses.create(
                input=query,
                extra_body={"agent": {"name": agent_name, "type": "agent_reference"}}
            )
            
            logger.debug(f"Agent {agent_name} response status: {response.status}")
            
            # Check response status
            if response.status == "failed":
                error_msg = f"Agent response failed"
                if hasattr(response, 'error') and response.error:
                    error_msg = f"{error_msg}: {response.error}"
                logger.error(f"Agent {agent_name} failed: {error_msg}")
                
                if attempt < MAX_RETRIES - 1:
                    backoff_time = BASE_BACKOFF_DELAY * (2 ** attempt)
                    logger.info(f"Retrying in {backoff_time} seconds (attempt {attempt+1}/{MAX_RETRIES})")
                    await asyncio.sleep(backoff_time)
                    continue
                    
                return f"Error: {error_msg}"
            
            # Format and return the response
            return _format_agent_response(response)
            
        except ResourceNotFoundError as e:
            logger.error(f"Resource not found error for agent {agent_name}: {str(e)}")
            return f"Error: The agent {agent_name} could not be found or accessed."
            
        except (ServiceRequestError, HttpResponseError) as e:
            logger.error(f"Service error querying agent {agent_name} (attempt {attempt+1}/{MAX_RETRIES}): {str(e)}")
            
            if attempt < MAX_RETRIES - 1:
                backoff_time = BASE_BACKOFF_DELAY * (2 ** attempt)
                logger.info(f"Retrying in {backoff_time} seconds")
                await asyncio.sleep(backoff_time)
            else:
                logger.error(f"Failed to query agent {agent_name} after {MAX_RETRIES} attempts")
                return f"Error: Failed to get a response after multiple attempts: {str(e)}"
                
        except Exception as e:
            logger.error(f"Unexpected error querying agent {agent_name}: {str(e)}")
            if attempt < MAX_RETRIES - 1:
                backoff_time = BASE_BACKOFF_DELAY * (2 ** attempt)
                logger.info(f"Retrying in {backoff_time} seconds")
                await asyncio.sleep(backoff_time)
            else:
                raise

    # This should not be reached if retries are working correctly
    return "Error: Failed to get a response after multiple attempts."


def _format_agent_response(response) -> str:
    """
    Format the agent's response from the responses API.
    
    Args:
        response: The Response object from the OpenAI responses API
        
    Returns:
        str: Formatted response text with citations if available
    """
    if not response:
        return "No response received from the agent."
    
    # Use the output_text property if available
    if hasattr(response, 'output_text') and response.output_text:
        result = response.output_text
    else:
        # Fall back to extracting text from output items
        result = ""
        if hasattr(response, 'output') and response.output:
            for item in response.output:
                # Check for message type items with text content
                if hasattr(item, 'content'):
                    for content_part in item.content:
                        if hasattr(content_part, 'text'):
                            result += content_part.text + "\n"
                elif hasattr(item, 'text'):
                    result += item.text + "\n"
    
    if not result:
        return "No text response received from the agent."
    
    return result.strip()


def to_snake_case(text: str) -> str:
    """
    Convert a string to snake_case, preserving existing underscores.
    
    Args:
        text: The text to convert
        
    Returns:
        str: The text converted to snake_case
    """
    # Replace non-alphanumeric characters (except underscores) with spaces
    text = re.sub(r'[^a-zA-Z0-9_\s]', '', text)
    # Replace spaces and runs of underscores with a single underscore and convert to lowercase
    return re.sub(r'[\s_]+', '_', text).lower()


def create_agent_tool(agent: AgentDetails, function_name: str, description: str) -> None:
    """
    Create a tool for an agent and register it with the MCP framework.
    
    Args:
        agent: The AgentDetails object from the project
        function_name: The function name to use for the tool
        description: The description/instructions for the agent
    """
    agent_name = agent.name  # Capture for closure
    
    async def agent_tool(query: str, ctx: Context = None) -> str:
        """Query the specified Azure AI Agent."""
        if not server_initialized:
            return "Error: Azure AI Agent server is not initialized. Check server logs for details."

        try:
            response = await query_agent(agent_name, query)
            return f"## Response from {agent_name} Agent\n\n{response}"
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error in agent tool {function_name}: {error_msg}")
            return f"Error querying {agent_name} agent: {error_msg}"
        
    # Set function metadata
    agent_tool.__name__ = function_name
    agent_tool.__doc__ = description or f"Query the {agent_name} agent"
    
    # Register with MCP framework
    mcp.add_tool(
        fn=agent_tool,
        name=function_name,
        description=description or f"Query the {agent_name} agent"
    )
    
    # Store in registry for tracking (use name as key since project agents use names)
    registered_agents[agent_name] = {
        "name": agent_name,
        "description": description,
        "function_name": function_name
    }


def _get_agent_description(agent: AgentDetails) -> str:
    """
    Extract the description/instructions from an AgentDetails object.
    
    Args:
        agent: The AgentDetails object
        
    Returns:
        str: The agent's description or instructions
    """
    try:
        # Try to get instructions from the latest version's definition
        if hasattr(agent, 'versions') and agent.versions:
            latest = agent.versions.get('latest', {})
            if isinstance(latest, dict):
                definition = latest.get('definition', {})
                if isinstance(definition, dict):
                    instructions = definition.get('instructions', '')
                    if instructions:
                        return instructions
        # Fall back to description if no instructions found
        if hasattr(agent, 'description') and agent.description:
            return agent.description
    except Exception as e:
        logger.debug(f"Could not extract description for agent {agent.name}: {e}")
    
    return f"Query the {agent.name} agent"


async def sync_agents() -> Dict[str, AgentDetails]:
    """
    Sync agents between Azure AI Foundry Project and MCP tools.
    
    This function queries the Azure AI Foundry Project for available agents,
    registers new agents as tools, updates existing agents, and removes
    deleted agents.
    
    Returns:
        Dict[str, AgentDetails]: Dictionary mapping agent names to their AgentDetails objects
    """
    if not server_initialized:
        logger.warning("Cannot sync agents: Server not initialized.")
        return {}

    try:
        # Get current agents from the project
        current_agents: Dict[str, AgentDetails] = {}
        agent_count = 0
        
        # Process the AsyncIterable response from AIProjectClient.agents.list()
        async for agent in project_client.agents.list():
            current_agents[agent.name] = agent
            agent_count += 1
            
        if agent_count == 0:
            logger.warning("No agents found in the Azure AI Foundry Project.")
            return current_agents
            
        logger.info(f"Found {agent_count} agents in Azure AI Foundry Project")
        
        # Add or update agents
        _add_or_update_agents(current_agents)
        
        # Remove deleted agents
        _remove_deleted_agents(current_agents)
                
        return current_agents
        
    except Exception as e:
        logger.error(f"Failed to sync agents: {str(e)}")
        return {}


def _add_or_update_agents(current_agents: Dict[str, AgentDetails]) -> None:
    """
    Add new agents as tools and update existing agents if needed.
    
    Args:
        current_agents: Dictionary of active agents from Azure AI Foundry Project
    """
    for agent_name, agent in current_agents.items():
        description = _get_agent_description(agent)
        
        if agent_name not in registered_agents:
            # New agent - add it
            function_name = to_snake_case(agent_name)
            create_agent_tool(agent, function_name, description)
            
            logger.info(f"Added agent tool: {agent_name} (Function: {function_name})")
            logger.debug(f"Agent details - Name: {agent_name}, Description: {description[:100]}...")
            
        elif description != registered_agents[agent_name].get("description", ""):
            # Update existing agent if description changed
            old_function_name = registered_agents[agent_name]["function_name"]
            mcp.remove_tool(old_function_name)
            
            # Create updated tool
            function_name = to_snake_case(agent_name)
            create_agent_tool(agent, function_name, description)
            
            logger.info(f"Updated agent tool: {agent_name} (old function: {old_function_name}, new function: {function_name})")


def _remove_deleted_agents(current_agents: Dict[str, AgentDetails]) -> None:
    """
    Remove tools for agents that no longer exist in Azure AI Foundry Project.
    
    Args:
        current_agents: Dictionary of active agents from Azure AI Foundry Project
    """
    for agent_name in list(registered_agents.keys()):
        if agent_name not in current_agents:
            function_name = registered_agents[agent_name]["function_name"]
            
            logger.info(f"Removing agent tool: {agent_name} (function: {function_name})")
            mcp.remove_tool(function_name)
            del registered_agents[agent_name]


async def register_agents() -> None:
    """Register all available agents as MCP tools."""
    logger.info("Registering agents as tools...")
    await sync_agents()
    logger.info(f"Registered {len(registered_agents)} agents as MCP tools")


async def update_tools() -> None:
    """Update tools based on changes in the Azure AI Agents."""
    logger.debug("Checking for agent updates...")
    current_agents = await sync_agents()
    logger.debug(f"Agent sync complete, {len(current_agents)} agents available")


async def periodic_update_task() -> None:
    """Run the update_tools function periodically."""
    while True:
        try:
            await asyncio.sleep(update_interval)
            logger.debug(f"Running scheduled agent sync (interval: {update_interval}s)")
            await update_tools()
        except asyncio.CancelledError:
            logger.info("Periodic update task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in periodic update task: {str(e)}")


async def shutdown() -> None:
    """Perform cleanup operations before server shutdown."""
    logger.info("Shutting down Azure AI Agent MCP Server...")
    # Add any cleanup code here if needed


async def main() -> None:
    """Main entry point for the async server."""
    # Register agents
    await register_agents()
    
    # Start the periodic update task
    update_task = asyncio.create_task(periodic_update_task())
    logger.info(f"MCP server is running with periodic updates every {update_interval} seconds")
    
    try:
        # Run the MCP server
        if server_type == "web":
            await mcp.run_async(transport="streamable-http", host="0.0.0.0", port=server_port, path=server_path)
        else:
            await mcp.run_async()
    except Exception as e:
        logger.error(f"Server error: {str(e)}")
    finally:
        # Ensure cleanup on exit
        update_task.cancel()
        try:
            await update_task
        except asyncio.CancelledError:
            pass
        
        await shutdown()


# Initialize MCP server
mcp = FastMCP(name="azure-agent")
server_initialized = initialize_server()

if __name__ == "__main__":
    status = "successfully initialized" if server_initialized else "initialization failed"
    logger.info(f"{'='*50}")
    logger.info(f"Azure AI Agent MCP Server {status}")
    
    if not server_initialized:
        logger.error("Server initialization failed. Exiting...")
        sys.exit(1)
        
    logger.info("Starting server...")
    
    # Run the main async function
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Uncaught exception: {str(e)}")
        sys.exit(1)
