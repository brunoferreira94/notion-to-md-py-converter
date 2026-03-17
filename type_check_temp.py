import py_compile
import sys
files=['D:\\Revelo\\notion-to-md-py-converter\\notion_converter.py','D:\\Revelo\\notion-to-md-py-converter\\notion_utils.py','D:\\Revelo\\notion-to-md-py-converter\\notion_converter_helpers.py']
errors=[]
for f in files:
    try:
        py_compile.compile(f, doraise=True)
    except Exception as e:
        errors.append(f+': '+str(e))
if errors:
    print('\n'.join(errors))
    sys.exit(2)
else:
    print('OK')
