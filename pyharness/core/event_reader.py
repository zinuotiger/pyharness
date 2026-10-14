"""Read an existing session event surface without creating another log."""
def session_events(session):
    if session is None:
        return []
    if hasattr(session, 'events_after'):
        return list(session.events_after(0))
    if hasattr(session, 'replay'):
        return list(session.replay())
    return list(getattr(session, 'events', ()))
