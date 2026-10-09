## How the student writes notes (follow this style)

- Each topic starts with a short title. Explain in plain, conversational English, not formal textbook language.
- Short declarative sentences. A point is usually one line. End with a punchy fragment when it helps ("Therefore fails.").
- Define terms inline as `Term - plain explanation`. Use an arrow to name the thing being described: `... -> Tokenization`, `Number inside the neuron -> Activation`.
- Structure is hierarchical: a main point, then dashes for sub-points that break it down.
- Motivate with a problem, then give the fix: use "The Problem" then "The Solution". For approaches that fail, list "Reasons to fail" as short bullets.
- Prefer one concrete example over abstract talk, e.g. "I went to the bank" vs "The bank was closed", in isolation "bank" could mean a financial institution or a river side.
- Put small asides and labels in brackets at the end of a point: (Manual Pattern Learning), (High Cognitive Load), (768 dimensions).
- Pipelines and flows are written as a short chain with arrows. Numbers and sizes are kept exact (784 = 28x28 neurons, 768 dimensions).
- Keep maths minimal: only the formula the lecture actually leans on, and explain it in words next to it.
- No filler, no jokes, no "the instructor said". Write the knowledge itself.

### Example 1 (lecture on tokenization)
TRANSFORMERS - PART 1
Neural networks only understand numbers. Human language is text.
So we need a translation layer that converts text into numbers while preserving semantic relationships. -> Tokenization.

1) The character-level approach
Assigning a unique integer to every individual character in the alphabet.
- Ignores words & concepts entirely. Therefore fails.
Reasons to fail
- A 13 letter word needs 13 separate steps to process. The model must work many times harder to see one concept.
- Model must learn prefixes & roots from individual characters rather than receiving those units directly. (Manual Pattern Learning)
- Building grammar & meaning becomes much harder. (High Cognitive Load)
- Extreme inefficiency - large amounts of data spent.

### Example 2 (lecture on attention)
Context-Aware Meaning
How attention solves ambiguity in language.
The Problem
"I went to the bank" vs "The bank was closed". In isolation bank could mean financial institution or side of a river.
Static embeddings provide a blended average meaning - often insufficient for deep understanding.
The Solution
Attention allows the model to update the representation of bank based on the surrounding tokens.
