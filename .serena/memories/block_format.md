# Block Format

Blocks are pure information resources that creating-skills uses to build target skills. They describe concepts, not prescriptive templates.

## Philosophy

Blocks are **information**, not **instructions**. They tell the agent what something IS, not exactly how to write it. The creating-skills skill takes this information and generates the appropriate output for the target skill based on its goals.

**Old approach (prescriptive):**
- "Here's a template, modify it"
- Examples to copy
- Cookie-cutter output

**New approach (informational):**
- "Here's what you need to know"
- Agent understands the concept
- Output shaped by target skill's goals

## Block Format

Every block follows this 7-section structure:

### 1. Overview
Why this exists and how it works as a whole. The big picture. Explains the pattern and purpose so the agent understands what they're trying to achieve.

### 2. Concept
Pure definition — one or two sentences stating what this thing IS.

### 3. Components
The parts that make it up. Typically a table:
| Component | Purpose |
|-----------|---------|
| Name | What it's for |

### 4. Characteristics
Principles — what makes this thing what it is. Bullet list of essential traits. These help the agent recognize when they're doing it right.

### 5. Variations
How this concept can differ based on need. Shows the possibilities without prescribing which to use. Typically a table:
| Variation | When | Description |
|-----------|------|-------------|

### 6. Application
How to use it — mechanics, syntax, where it appears in a skill. This is the "how" section but still informational:
- Tag syntax (if applicable)
- Placement guidance
- Language patterns
- Where it typically appears in skill structure

This section bridges concept → implementation without being a copy-paste template.

### 7. Relationships
How this concept connects to other concepts. Helps the agent understand the ecosystem:
| Related Concept | Distinction |
|-----------------|-------------|

## Creating a New Block

1. **Start with Overview** — Write the "why" and "how it works as a whole" first. If you can't explain the big picture, you don't understand the concept well enough.

2. **Define the Concept** — One clear sentence. What IS this thing?

3. **Identify Components** — What parts make it up? Not "what to write" but "what pieces exist."

4. **Extract Characteristics** — What principles define this? What makes it work? What makes it fail?

5. **Map Variations** — How does this concept manifest differently based on need? Show the spectrum.

6. **Document Application** — Now the practical: syntax, placement, patterns. Concrete enough to use, not prescriptive enough to copy blindly.

7. **Draw Relationships** — How does this connect to other concepts? What's it similar to? Different from?

## Key Principle

The creating-skills skill reads these blocks, understands the concepts, and then GENERATES the appropriate output for the target skill. The block doesn't dictate the output — the target skill's goals do.

A boundary block teaches what a boundary IS. The creating-skills skill decides:
- Whether the target skill needs a boundary
- What the boundary should constrain
- Where it should be placed
- How it should be worded

The block informs. The skill decides.

## Examples

See `blocks/control/boundary.md` and `blocks/control/checkpoint.md` for blocks in this format.
