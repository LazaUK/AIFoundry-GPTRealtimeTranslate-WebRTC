# Azure AI Foundry: Web App for Realtime Speech Translation via WebRTC

This repo demonstrates how to use the **gpt-realtime-translate** model in Microsoft Foundry with a server-side *WebRTC* SDP Proxy (in Python) and secure *Entra ID* authentication.

> [!CAUTION]
> This repo is provided to reproduce the observed issue. DON'T USE IT FOR YOUR LOCAL IMPLEMENTATION YET!!

## 📑 Table of Contents:
- [Part 1: Configuring Solution Environment](#part-1-configuring-solution-environment)
- [Part 2: Backend Implementation](#part-2-backend-implementation)
- [Part 3: Frontend UI](#part-3-frontend-ui)
- [Part 4: Running the Demo](#part-4-running-the-demo)

## Part 1: Configuring Solution Environment

### 1.1 Foundry Setup
Ensure you have a **gpt-realtime-translate** model deployed in your Microsoft Foundry resource. Take a note of your Foundry's resource name and your model's deployment name.

### 1.2 Authentication
The demo utilises passwordless *Microsoft Entra ID* authentication via the `DefaultAzureCredential` provider.

Before running the server locally, log in to your Azure environment using CLI command:

``` PowerShell
az login
```

> [!NOTE]
> Ensure the identity executing this code (your local *Azure CLI* login, *Service Principal* or *Managed Identity*) has been granted at least the **Foundry User** on your Azure AI resource, as described [here](https://learn.microsoft.com/en-us/azure/foundry/concepts/rbac-foundry?tabs=owner%2Cfoundry#minimum-role-assignments-to-get-started).

### 1.3 Environment Variables
Configure the following environment variables in your operating system:

| Environment Variable    | Description                                               |
| ----------------------- | --------------------------------------------------------- |
| FOUNDRY_RESOURCE_NAME   | The sub-domain name of your Foundry resource.             |
| FOUNDRY_DEPLOYMENT_NAME | The exact name of your gpt-realtime-translate deployment. |

1.4 Installation
Install the necessary Python packages:

``` Python
pip install fastapi uvicorn httpx azure-identity
```
