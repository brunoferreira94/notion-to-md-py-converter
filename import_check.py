import json
import traceback
modules = ['settings','notion_converter','convert_from_public','renderers','notion_utils']
results = {}
for m in modules:
    try:
        __import__(m)
        results[m] = {'ok': True, 'error': None}
    except Exception as e:
        results[m] = {'ok': False, 'error': traceback.format_exc()}
print(json.dumps(results))
