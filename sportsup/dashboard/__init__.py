"""Read-only admin dashboard (owner-only).

A small FastAPI app showing who has onboarded, what tournaments and teams each user
follows, and popularity aggregates. Binds to localhost and is reached via an SSH tunnel,
with HTTP Basic auth as a second layer — it only ever issues SELECTs.
"""
