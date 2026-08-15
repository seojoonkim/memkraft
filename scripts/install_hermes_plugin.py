#!/usr/bin/env python3
"""Source-checkout wrapper for the installed memkraft-hermes-install command."""
from memkraft.hermes_install import install_bridge, main

__all__ = ["install_bridge", "main"]

if __name__ == "__main__":
    main()
