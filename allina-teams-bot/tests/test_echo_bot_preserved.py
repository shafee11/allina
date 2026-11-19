import inspect
from microsoft_teams.apps import App
import src.main as main_module

def test_main_app_instantiated():
    """Verify that main.py defines an App instance running in Socket Mode."""
    assert hasattr(main_module, "app"), "main.py must define 'app'"
    assert isinstance(main_module.app, App), "main.app must be an instance of App"
    assert main_module.app.socket_mode is not None, "main.app must have socket_mode configured"

def test_echo_handlers_preserved():
    """Verify greeting and echo handlers are defined in main.py."""
    assert hasattr(main_module, "handle_greeting"), "handle_greeting must be preserved"
    assert hasattr(main_module, "handle_message"), "handle_message must be preserved"
    assert inspect.iscoroutinefunction(main_module.handle_greeting)
    assert inspect.iscoroutinefunction(main_module.handle_message)
