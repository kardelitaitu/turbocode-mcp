"""Fix all unclosed open() calls in test_core.py."""
import re

path = r'C:\My Script\turbocode-mcp\tests\test_core.py'
with open(path) as f:
    content = f.read()

lines = content.split('\n')

def find_matching_paren(text, start):
    """Find position of closing paren matching opening paren at start."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i
    return -1

def fix_line(line, i):
    stripped = line.rstrip()
    if 'open(' not in stripped:
        return line
    if stripped.strip().startswith('with ') or stripped.strip().startswith('#'):
        return line
    
    # Only handle json.dump and json.load patterns
    if not ('json.dump(' in stripped or 'json.load(' in stripped):
        return line
    
    # Find all open() calls
    result = stripped
    while True:
        idx = result.find('open(')
        if idx == -1:
            break
        
        # Find the matching close paren
        close = find_matching_paren(result, idx + 4)
        if close == -1:
            break
        
        open_call = result[idx:close + 1]
        # Check if this open() is inside a json.dump() or json.load()
        # Find the enclosing json call
        json_start = max(result.rfind('json.dump(', 0, idx), result.rfind('json.load(', 0, idx))
        if json_start == -1:
            break
        
        json_close = find_matching_paren(result, json_start + len('json.dump(' if 'json.dump(' in result[json_start:] else 'json.load('))
        if json_close == -1:
            break
        
        # We have the full json.dump(x, open(...)) or json.load(open(...))
        full_expr = result[json_start:json_close + 1]
        
        # Extract the leading whitespace
        leading = re.match(r'^(\s*)', result).group(1)
        
        if full_expr.startswith('json.dump('):
            # json.dump(EXPR, open(PATH, MODE))
            # Find the comma before open
            open_idx = full_expr.rfind(', open(')
            if open_idx == -1:
                break
            expr = full_expr[len('json.dump('):open_idx]
            open_part = full_expr[open_idx + 2:]  # ", open(...)"
            open_inner = open_part[len('open('):-1]  # PATH, MODE
            parts = [p.strip() for p in open_inner.rsplit(',', 1)]
            filepath = parts[0]
            mode = parts[1].strip('"').strip("'") if len(parts) > 1 else 'w'
            
            replacement = f'with open({filepath}, "{mode}") as f:\n{leading}    json.dump({expr}, f)'
            result = result[:json_start] + replacement + result[json_close + 1:]
        elif full_expr.startswith('json.load('):
            # json.load(open(PATH))
            open_inner = open_call[len('open('):-1]
            filepath = open_inner.split(',')[0].strip()
            
            replacement = f'with open({filepath}) as f:\n{leading}    json.load(f)'
            result = result[:json_start] + replacement + result[json_close + 1:]
        else:
            break
        
        # Continue looking for more open() on the same line
        continue
    
    if result != stripped:
        return result + '\n'
    return line

new_lines = []
for i, line in enumerate(lines):
    new_lines.append(fix_line(line, i))

with open(path, 'w') as f:
    f.write('\n'.join(new_lines))

print('Done')
