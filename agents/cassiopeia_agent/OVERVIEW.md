# OVERVIEW OF THE CASSIOPEIA AGENT

This document outlines the core functionality, architecture, and usage of the cassiopeia agent.

## Functionality

The cassiopeia agent serves as the central nervous system of our multi-agent system. Its primary responsibilities include:

1.  **Intent Recognition and Analysis:** Understanding user requests and breaking them down into actionable tasks.
2.  **Task Cassiopeiation:** Planning and sequencing tasks for various specialized agents.
3.  **Agent Coordination:** Managing the lifecycle and communication between different agents.
4.  **Information Synthesis:** Aggregating and presenting results from multiple agents.
5.  **Context Management:** Maintaining conversational context and state.
6.  **LLM Interaction:** Leveraging large language models for various sub-tasks, including planning, analysis, and response generation.

## Architecture

The agent follows a modular design, with distinct components responsible for specific functions. Key modules include:

*   **`main.py`:** Entry point and main application logic, including FastAPI server setup.
*   **`manager.py`:** Handles the core cassiopeiation logic, agent lifecycle, and communication.
*   **`nlu_engine.py`:** Processes natural language understanding tasks.
*   **`state_manager.py`:** Manages the state of the system and ongoing tasks.
*   **`health_monitor.py`:** Monitors the health and responsiveness of other agents.
*   **`app_context.py`:** Provides application-wide context and dependencies.
*   **`auth.py`:** Handles authentication and authorization.
*   **`registry.py`:** Manages the registration and discovery of agents.
*   **`marketplace_handler.py`:** Interacts with the agent marketplace.
*   **`sandbox_tool.py`:** Manages sandboxed execution environments.
*   **`rate_limiter.py`:** Implements rate limiting for agent interactions.
*   **`error_messages.py`:** Centralizes error message definitions.
*   **`models.py`:** Defines data structures and Pydantic models used throughout the agent.

## Usage

### Running the Cassiopeia Agent

The agent can be run as a FastAPI application.

**Development Mode (local LLM):**
```bash
python agents/cassiopeia_agent/main.py --llm local
```

**Production Mode (external LLMs):**
```bash
LLM_BACKEND=chatgpt python agents/cassiopeia_agent/main.py
LLM_BACKEND=claude python agents/cassiopeia_agent/main.py
```

**Running as a module:**
```bash
python -m agents.cassiopeia_agent.main
```

### Key Features and Commands

*   **Intent Analysis:** The agent analyzes user intents to determine the best course of action.
*   **Agent Dispatch:** Based on the analyzed intent, the agent dispatches requests to appropriate specialized agents.
*   **LLM Integration:** The agent integrates with various LLM providers (local, ChatGPT, Claude, Gemini) for advanced reasoning capabilities.
    *   Example command: `python -m agents.cassiopeia_agent.main --llm gemini`

## Contribution

Please refer to the main project's `CONTRIBUTING.md` for details on how to contribute.

## License

This project is licensed under the Apache 2.0 License.

## Notes

*   The agent relies on Redis for message brokering. Ensure Redis is running and accessible.
*   Environment variables can be used to configure LLM backends and other settings.
*   The `agents/cassiopeia_agent` directory contains the primary logic for the cassiopeiator.
