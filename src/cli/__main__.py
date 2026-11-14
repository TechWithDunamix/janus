"""``python -m cli`` — the same console as the ``janus`` command.

Useful when the virtualenv's ``bin/`` is not on ``PATH`` (cron jobs, containers,
``sudo -u janus /opt/janus/.venv/bin/python -m cli migrate``).
"""

import sys

from cli import main

sys.exit(main())
