"""Janus's domain services.

Everything above the models and below the routes. A route handler's job is to
authorise, parse and render; the decision about what actually happens lives
here, so the same operation reached from the dashboard, the JSON API and the
CLI is literally the same function.
"""
