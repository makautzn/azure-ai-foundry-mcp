# Azure AI Foundry Agent MCP

This project provides an MCP (Model Context Protocol) server for integrating with Azure AI Foundry Project agents. It is designed to help you interact with Azure-hosted AI agents (created in the Foundry portal), query them, and manage agent-related workflows in a secure and scalable way.

## Features
- Automatic discovery and registration of all Azure AI Project agents from your Foundry project
- Dynamically creates MCP tools for each agent in your Azure AI Foundry Project
- Supports both local and web transport modes
- Regular background sync to detect new or changed agents
- Uses Azure AI Projects SDK v2 (`azure-ai-projects==2.0.0b3`) with separate `azure-ai-agents` package

## Project Structure
- `azure_agent_mcp_server/` — Main server code and tools
- `.env` — Environment variables for configuration (see below)
- `pyproject.toml` — Project dependencies and metadata
- `uv.lock` — Lockfile for reproducible installs (managed by [uv](https://github.com/astral-sh/uv))

## Notes
- **SDK Version**: This project uses `azure-ai-projects==2.0.0b3` and `azure-ai-agents>=1.1.0`
- **Agent Types**: Works with **Project agents** (prompt-based agents created in the Azure AI Foundry portal), not classic agents created programmatically
- **Project Requirement**: Requires an Azure AI Foundry Project. Hub-based projects are not supported.
- For more information about Azure AI Foundry project types, see the [official documentation](https://learn.microsoft.com/en-us/azure/ai-foundry/what-is-azure-ai-foundry#project-types).

## Getting Started

### 1. Prerequisites
- Python 3.13+
- [uv](https://github.com/astral-sh/uv) (recommended for dependency management)
- Azure AI Foundry project (no hub-based projects supported)
- You get the project endpoint from the Azure AI Foundry portal. It looks like this:
  ```
  https://<your-ai-foundry-project-ressource>.services.ai.azure.com/api/projects/<your-ai-foundry-project-name>
  ```
- You can find the project endpoint in the Azure AI Foundry portal under "Overview":
  ![AI Foundry Endpoint Location](assets/ai-foundry-endpoint.png)

### 2. Setup
1. **Clone the repository**
2. **Configure environment variables:**
   - Copy the provided `.env` file or create your own. Example:
     ```env
     PROJECT_ENDPOINT=your-ai-foundry-project-endpoint
     ```
   - This variable is required for connecting to your Azure AI Agent Service.

3. **Install dependencies:**
   - Using [uv](https://github.com/astral-sh/uv):
     ```sh
     uv pip install -r pyproject.toml
     ```
   - Or, to sync with the lockfile:
     ```sh
     uv sync
     ```
   - Alternatively, you can use `pip` or `pipx` if you prefer.

### 3. Running the Server
The server can run in two modes:

* **Local mode** (default):
  ```sh
  uv run -m azure_agent_mcp_server  
  # Alternatively, you can run:
  # python -m azure_agent_mcp_server
  ```

* **Web mode** (accessible via HTTP):
  ```sh
  # Set SERVER_TYPE=web in your .env file, or run with:
  SERVER_TYPE=web uv run -m azure_agent_mcp_server 
  # Alteratively, you can run:
  # SERVER_TYPE=web python -m azure_agent_mcp_server
  ```

When started, the server will:
1. Connect to Azure AI Foundry Project using the provided endpoint
2. Automatically discover all your project agents (created in Foundry portal)
3. Create MCP tools for each agent using the OpenAI responses API
4. Periodically check for new or updated agents every 300 seconds

### 4. Querying Agents in VSCode / GitHub Copilot
1. Add MCP Server to VSCode settings:
   ```json
   "mcp": {
        "servers": {
            "Azure AI Agents Server": {
                "command": "uv",
                "args": [
                    "--directory",
                    "/YOUR/PROJECT/PATH",
                    "run",
                    "-m",
                    "azure_agent_mcp_server"
                ],
                "env": {
                    "PROJECT_ENDPOINT": "your-ai-foundry-project-endpoint"
                }
            }
        }
    },
   ```

2. After the server starts, it automatically discovers all agents from your Azure AI Foundry Project and makes them available as MCP tools with names based on the agent names (converted to snake_case).

3. You can then use these tools directly in GitHub Copilot or any other MCP-compatible client.

4. **Good to know:** Create a copilot-instructions.md file in the .github directory in your project to instruct copilot to streamline the usage of the MCP tools. For more information about repository custom instructions, see the [GitHub documentation](https://docs.github.com/en/copilot/customizing-copilot/adding-repository-custom-instructions-for-github-copilot#repository-custom-instructions-example).

## Environment Variables and Configuration

The MCP server can be configured using the following environment variables in your `.env` file:

- `PROJECT_ENDPOINT`: Azure AI Foundry project endpoint (required)
- `SERVER_TYPE`: Set to "local" (default) or "web" to choose the transport mode
- `SERVER_PORT`: Port number for web mode (default: 8000)
- `SERVER_PATH`: Path for web mode (default: "/")
- `UPDATE_INTERVAL`: How often (in seconds) to check for new or updated agents (default: 300)
- `LOG_LEVEL`: Set the logging level (default: "WARNING"). Options include "DEBUG", "INFO", "WARNING", "ERROR", and "CRITICAL".

Example `.env` file:
```env
PROJECT_ENDPOINT=your-ai-foundry-project-endpoint
SERVER_TYPE=web
SERVER_PORT=9000
UPDATE_INTERVAL=120
LOG_LEVEL=INFO
```

**Note**: Never commit secrets to version control.

## About uv
[uv](https://github.com/astral-sh/uv) is a fast, modern Python package and project manager. It replaces tools like `pip`, `pip-tools`, `pipx`, `poetry`, and `virtualenv`, and is recommended for reproducible, efficient dependency management in this project.

- See [uv documentation](https://docs.astral.sh/uv/) for more details.

## How Agent Tools Work

The system automatically:

1. Connects to Azure AI Foundry Project on startup using `AIProjectClient`
2. Discovers all project agents available in your Foundry project via `project_client.agents.list()`
3. Creates an MCP tool for each agent, converting the agent name to snake_case for the function name
4. Extracts the agent's instructions/description from the latest version definition
5. Uses the OpenAI responses API with agent references to query agents
6. Periodically checks for new, updated, or deleted agents
7. Updates the available tools accordingly

Example:
- An agent named "Coding Guidelines" becomes a tool named `coding_guidelines`
- An agent named "Python Expert" becomes a tool named `python_expert`

## Technical Details

### SDK v2 Changes
This project has been migrated to Azure AI Projects SDK v2 (`azure-ai-projects==2.0.0b3`), which introduces significant changes:

#### Architecture
- **Client**: Uses `AIProjectClient` from `azure.ai.projects` instead of `AgentsClient` from `azure.ai.agents`
- **Agent Discovery**: Uses `project_client.agents.list()` to discover project agents
- **Agent Invocation**: Uses OpenAI responses API (`openai_client.responses.create()`) with agent references instead of the classic threads/messages/runs workflow
- **Agent Identification**: Agents are identified by **name** rather than ID

#### Agent Types
The SDK v2 distinguishes between two types of agents:
1. **Classic agents** - Created programmatically via `AgentsClient`, use threads/messages/runs API
2. **Project agents** (used by this server) - Created in Azure AI Foundry portal, use responses API with agent references

This MCP server specifically supports **Project agents** (prompt-based agents) created in the Foundry portal.

### Dependencies
- `azure-ai-projects==2.0.0b3` - Main Azure AI Foundry SDK
- `azure-ai-agents>=1.1.0` - Agent models and types (now a separate package in v2)
- `openai>=2.16.0` - Required for responses API
- `fastmcp>=2.3.5` - MCP server framework

## License
This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.