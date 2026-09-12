"""Null feature layer.

`A-precision` is deterministic by construction, not by configuration: this
package replaces the hybrid LLM layer with inert stand-ins that expose the same
names the orchestrator imports.  There is no provider, no prompt, no network
call and no API key anywhere in this package -- the capability is absent, so it
cannot be switched on by a stray environment variable.

`B-coverage` ships the real layer behind the same interface.
"""
