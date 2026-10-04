"""Exceptions the agent raises deliberately.

Every one of these is safe to show in the dashboard: they describe a refusal or a
condition the user can act on, never a stack trace. Anything unexpected is logged
locally and reported as a generic failure so an internal path never leaks.
"""


class AgentError(Exception):
    """Base class. `user_message` is what the dashboard shows."""


class ConfigError(AgentError):
    """agent.json is missing something required to start."""


class AuthError(AgentError):
    """The backend rejected this agent's credentials."""


class BackendError(AgentError):
    """The backend was unreachable or answered unusably."""


class RateLimited(AgentError):
    """The backend asked this agent to slow down."""


class CommandRejected(AgentError):
    """A command was refused locally: not allowlisted, or bad arguments.

    This is the important one for security. Nothing from the network is ever
    turned into a shell string, so a command name outside `commands.ALLOWED` or an
    argument outside its documented bounds stops here.
    """


class ServerNotInstalled(AgentError):
    """The server directory has no jars yet."""


class OperationTimeout(AgentError):
    """A local operation exceeded its budget; the caller should report failure."""


class TunnelNotConfigured(AgentError):
    """A tunnel provider was requested but has no usable configuration."""