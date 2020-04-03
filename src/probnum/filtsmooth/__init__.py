"""
Import convenience functions in interface.py to create an
intuitive, numpy-like interface.

Note
----
Local import, because with a global import this does not seem
to work.
"""
from .interface import *
from src.probnum.filtsmooth.statespace.continuous import *
from src.probnum.filtsmooth.statespace.discrete import *