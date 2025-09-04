### Salesforce MCP Server Reference App

This repository contains a reference implementation of a AI Agent that interacts with Salesforce Sales objects and provides answers about opportunities, leads,  and accounts. It exposes Salesforce APIs using Model Context Protocol(MCP) which consumed by MCP Client.  It provides a FastAPI-based backend to handle user queries, manage sessions and a react-based frontend for user interaction.

### Prerequisites

- Python 3.11+.
- Azure Foundry Resource with Azure OpenAI Model deployment. 
- Salesforce Account with API access.
- Bash Terminal.
- Az CLI installed and configured.
- Azure Login configured with `az login`.
- Azure Login credential should have Azure AI Developer role assigned in Azure AI Foundry as shown below.![alt text](image.png)



### Setup Instructions

1. **Clone the Repository**:
   ```bash
   git clone <repository-url>
   cd sales_force_ai_agent_service
   chmod +x run1.sh
   ```

2. **Azure Login**
   ```bash
   az login
   ```

3. **Navigate to the Project Directory**:
   ```bash
   cd sales_force_ai_agent_service/backend/sf_mcp_server
   ```
   Update the .env file with your Salesforce credentials.

4. **Navigate to the Project Directory**:
   ```bash
   cd sales_force_ai_agent_service/backend/agent_api_server
   ```
   Update the .env file with your Azure OpenAI and Foundry credentials.

5. **Run Install Script**: This will set up virtual environments, install dependencies for backend and frontend and start the servers.
   ```bash
   cd sales_force_ai_agent_service
   bash run1.sh
   ```

6. **Access the Application**: Open your web browser and navigate to `http://localhost:5173` to access the frontend interface.

### Troubleshooting

- Node error:
   ```bash
   brew update
   brew reinstall node
   ```

### Demo Screen Shots

![alt text](Screenshot1.png)
![alt text](Screenshot2.png)
![alt text](Screenshot3.png)


Salesforce Demo Account:
![alt text](sf_screenshot.png)