---
description: Bump SimLaps Client version across all files
---

# Bump Version Workflow

When bumping the SimLaps Client version, you MUST update ALL of these 4 files:

## Files to Update

### 1. C:\Storage\my documents\sim-laps-client\src\version.py
Update the version constants:
```python
VERSION_MAJOR = 1
VERSION_MINOR = 1
VERSION_PATCH = 9
```

### 2. C:\Storage\my documents\sim-laps-app\lib\version.ts
Update the version export:
```typescript
export const LATEST_CLIENT_VERSION = '1.1.9'
```

### 3. C:\Storage\my documents\sim-laps-app\components\layout\footer.tsx
Update the download link (NO DOTS in exe name):
```tsx
href="/downloads/SimLapsClient-v119.exe"
```

### 4. C:\Storage\my documents\sim-laps-app\components\layout\header.tsx
Update all download links (NO DOTS in exe name):
```tsx
href="/downloads/SimLapsClient-v119.exe"
href="/downloads/SimLapsClient-v119.exe"
```

## Version Format Rules

| File | Format | Example |
|------|--------|---------|
| version.py | Separate integers | `VERSION_MAJOR = 1` |
| version.ts | Dotted string | `'1.1.9'` |
| footr.tsx | Concatenated | `v119` |
| header.tsx | Concatenated | `v119` |

## Example: Bumping from 1.1.9 to 1.2.0

**src/version.py:**
```python
VERSION_MAJOR = 1
VERSION_MINOR = 2  # Changed from 1
VERSION_PATCH = 0  # Changed from 9
```

**version.ts:**
```typescript
export const LATEST_CLIENT_VERSION = '1.2.2'
```

**footer.tsx & header.tsx:**
```tsx
href="/downloads/SimLapsClient-v120.exe"  # v119 -> v120
```

## Checklist

- [ ] src/version.py - VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH
- [ ] web/src/version.ts - LATEST_CLIENT_VERSION string  
- [ ] web/src/components/footr.tsx - download link filename
- [ ] web/src/components/header.tsx - all download link filenames
