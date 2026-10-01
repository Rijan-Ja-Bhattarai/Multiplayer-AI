from a2a.helpers import (
    get_message_text, 
    new_task_from_user_message, 
    new_text_message, 
    new_text_part 
)

from a2a.server.agent_execution import AgentExecutor, RequestContext 
from a2a.server.events import EventQueue 
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState 


class HelloWorldAgent:
    def invoke(self, user_message: str) -> str:
        return f"Message has been recieved: {user_message}"

class HelloWorldAgentExecutor(AgentExecutor):
    def __init__(self) -> None:
        self.agent = HelloWorldAgent()


    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # 1. Collect task from user request 
        if context.current_task: 
            task = context.current_task
        else:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        # 2. Update Task  Status in event queue 
        task_updater = TaskUpdater(
            event_queue=event_queue, task_id=task.id, context_id=context.context_id
        )
        await task_updater.update_status(
            state=TaskState.TASK_STATE_WORKING, 
            message=new_text_message('Processing request...')
        )

        # 3 Collect user request from request content and invoke LLM agent to generate content 
        query = get_message_text(context.message)
        if query:
            result = await self.agent.invoke(user_request=query)
        else:
            result = 'No input was provided in the request content.'

        # 4. Add generated response as an artifact to EventQueue 
        await task_updater.add_artifact(parts=[
            new_text_part(text=result, media_type='text/plain')
        ])
        print(f'Result: {result}')


        # 5. Update Task Status to completed in event queue 
        await task_updater.update_status(
            state=TaskState.TASK_STATE_COMPLETED, 
            message=new_text_message('Request processing completed.')
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Raise Exception as cancel is not supported for this agent"""
        raise NotImplementedError("Cancel is not supported for this agent")