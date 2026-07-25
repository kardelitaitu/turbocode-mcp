"""Replace json.dump(x, open(p, 'w')) with proper with-blocks across all test files."""
import re, os, glob

base = r'C:\My Script\turbocode-mcp\tests'

# Pattern: json.dump(EXPR, open(PATH, MODE_OPTIONAL))
# We replace with: with open(PATH, MODE) as f: json.dump(EXPR, f)
PATTERN = re.compile(r'json\.dump\(([^,]+),\s*open\(([^)]+)\)\)')

def fix_line(line):
    m = PATTERN.search(line)
    if not m:
        return line
    expr, path_arg = m.group(1), m.group(2)
    indent = ' ' * (len(line) - len(line.lstrip()))
    # Parse the open() call
    # open(path) or open(path, "w") or open(path, "wb")
    open_parts = [p.strip().strip('"').strip("'") for p in path_arg.split(',')]
    filepath = open_parts[0]
    mode = open_parts[1] if len(open_parts) > 1 else 'w'
    return f'{indent}with open({filepath}, "{mode}") as f:\n{indent}    json.dump({expr}, f)\n'

for fpath in sorted(glob.glob(os.path.join(base, 'test_*.py'))):
    with open(fpath) as f:
        content = f.read()
    
    lines = content.split('\n')
    new_lines = []
    changes = 0
    for line in lines:
        fixed = fix_line(line)
        if fixed != line:
            changes += 1
        new_lines.append(fixed)
    
    if changes:
        with open(fpath, 'w') as f:
            f.write('\n'.join(new_lines))
        print(f'{os.path.basename(fpath)}: {changes} fixes')
