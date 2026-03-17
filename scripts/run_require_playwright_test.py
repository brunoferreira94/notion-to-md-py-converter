import os
import sys

# Ensure current working directory is on sys.path so local modules are importable
cwd = os.getcwd()
if cwd not in sys.path:
    sys.path.insert(0, cwd)

import convert_from_public

# Force Playwright to be considered unavailable
convert_from_public.PLAYWRIGHT_AVAILABLE = False

# Prepare arguments to require Playwright (should cause exit if PLAYWRIGHT_REQUIRE logic triggers)
sys.argv = ['convert_from_public.py', '--require-playwright', '--page-url', 'http://example.com']

try:
    result = convert_from_public.main()
except SystemExit as e:
    print('EXIT_CODE:', e.code)
else:
    print('NO_EXIT')
