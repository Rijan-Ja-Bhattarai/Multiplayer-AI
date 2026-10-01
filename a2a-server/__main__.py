import uvicorn 

from a2a.server.request_handlers import DefaultRequestHandler 
from a2a.server.routes import ( 
    create_agent_card_routes, 
    create_jsonrpc_routes, 
)
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import ( 
    AgentCapabilities, 
    AgentCard, 
    AgentInterface, 
    AgentSkill,
)
from AgentExecutor import ( 
    HelloWorldAgentExecutor,
)
from starlette.applications import Starlette 

if __name__ == "__main__":
    # Define the abilities of the agnet which it can perform 
    skill = AgentSkill( 
        id='echo_bot', 
        name='Echo Bot',
        description='A simple echo bot that responds to user messages.',
        input_modes=['text/plain'],
        output_modes=['text/plain'],
        tags=['a2a', 'echo-example'], 
        examples=['hi', 'hello', 'yo'],
    )

    # Define private skills that are not shown on skill card 
    extended_skill = AgentSkill( 
        id='echo_bot_super_mode', 
        name='Echo Bot (Super Sayan)',
        description='A private skill that is not shown on the skill card', 
        tags=['a2a', 'echo-example', 'private'],
        examples=['WSP BIG DAWG', 'SUP TWIN'],
    )

    # Define a public facing agent card that allows clients to discover agents 
    public_agent_card = AgentCard(
        # Basic identity information for a2a server 
        name='Hello World Agent', # Identity 
        description='A simple hello world agent', 
        version='0.0.1', 
        default_input_modes=['text/plain'], # Supported media types 
        default_output_modes=['text/plain'],
        # Supported a2a features 
        capabilities=AgentCapabilities(
            streaming=True, 
            extended_agent_card=True
        ), 
        supported_interfaces=[
            AgentInterface(
                protocol_binding='JSONRPC', 
                url='http:127.0.0.1:9999',
                protocol_version='1.0.0',
            )
        ], 
        # The list of skills the agent offers 
        skills=[skill], 
    )

    # Defines the authenticated extended agent card with 
    # extneded skills that are visible to only authenticated users 
    extended_agent_card = AgentCard(
        name='Hello World Agent (Extended)', 
        description='A simple hello world agent with extended skills',
        version='0.0.2', 
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        capabilities=AgentCapabilities(
            streaming=True,
            extended_agent_card=True
        ),
        supported_interfaces=[
            AgentInterface(
                protocol_binding='JSONRPC',
                url='http:127.0.0.1:9999',
                protocol_version='1.0.0',
            )
        ],
        skills=[skill, extended_skill],
        icon_url=None,
    )

    # Setup Handler
    request_handler = DefaultRequestHandler(
        # Agent Executor handle 
        agent_executor=HelloWorldAgentExecutor(), 
        task_store=InMemoryTaskStore(),
        agent_card=public_agent_card,
        extended_agent_card=extended_agent_card
    )

    # Create routes to for a2a server 
    # routes handle incoming traffic from clients 
    routes = []

    # Create routes for agent card 
    routes.extend(create_agent_card_routes(public_agent_card))

    # Creates routes for JSONRPC protocol 
    routes.extend(create_jsonrpc_routes(request_handler, '/'))

    # Create a web app with defined routes 
    app = Starlette(routes=routes)

    # Run the app 
    uvicorn.run(app, host='127.0.0.1', port=9999)

