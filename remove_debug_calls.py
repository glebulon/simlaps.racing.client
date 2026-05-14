# Read the file
with open('src/core/log_parser.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Remove lines containing _debug.log or _debug.start
filtered_lines = []
for line in lines:
    if '_debug.log(' in line or '_debug.start()' in line:
        continue
    filtered_lines.append(line)

# Write back
with open('src/core/log_parser.py', 'w', encoding='utf-8') as f:
    f.writelines(filtered_lines)

print(f"Removed {len(lines) - len(filtered_lines)} lines containing _debug calls")
