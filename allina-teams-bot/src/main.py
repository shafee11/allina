import asyncio
import re

from microsoft_teams.api import MessageActivity, TypingActivityInput
from microsoft_teams.apps import ActivityContext, App
from src.allina.teams.message_handlers import AllinaTeamsDispatcher

app = App(socket_mode=True)
dispatcher = AllinaTeamsDispatcher()


@app.on_message_pattern(re.compile(r"hello|hi|greetings"))
async def handle_greeting(ctx: ActivityContext[MessageActivity]) -> None:
    """Handle greeting messages."""
    await ctx.send("Hello! How can I assist you today?")


@app.on_message
async def handle_message(ctx: ActivityContext[MessageActivity]):
    """Handle message activities using the ALLINA dispatcher with echo fallback."""
    await dispatcher.dispatch(ctx)


def main():
    asyncio.run(app.start())


if __name__ == "__main__":
    main()
