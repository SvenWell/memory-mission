"""Per-employee brain — the four-layer agent memory model.

Each employee's personal plane has five subdirectories under
``personal/<employee_id>/``:

- ``working/`` — current task state (volatile; archived after N days)
- ``episodic/`` — agent's event log (AGENT_LEARNINGS.jsonl, salience-ranked)
- ``semantic/`` — distilled patterns / curated pages (the wiki side
  every step before this built; ``page_path`` puts personal-plane
  curated content here)
- ``preferences/`` — how this employee wants their agent to behave
- ``lessons/`` — distilled takeaways the agent has learned

All five layers are vault-native — open the personal plane in
Obsidian and the layout makes sense. Same shape adapted from
agentic-stack's per-coding-agent memory model.

This package ships the working / episodic / preferences / lessons
primitives. The semantic layer (curated pages + KG) is already
covered by ``memory.pages`` + ``memory.engine`` + ``memory.knowledge_graph``
from earlier steps.
"""

from memory_mission.personal_brain.episodic import (
    EPISODIC_DIR,
    LEARNINGS_FILENAME,
    AgentLearning,
    EpisodicLog,
    episodic_dir,
    learnings_path,
    record_learning,
)
from memory_mission.personal_brain.lessons import (
    LESSONS_DIR,
    LESSONS_JSONL,
    LESSONS_MARKDOWN,
    Lesson,
    LessonsStore,
    jsonl_path,
    lesson_id,
    lessons_dir,
    markdown_path,
)
from memory_mission.personal_brain.preferences import (
    PREFERENCES_DIR,
    PREFERENCES_FILENAME,
    Preferences,
    preferences_dir,
    preferences_path,
    read_preferences,
    update_preferences,
    write_preferences,
)
from memory_mission.personal_brain.working import (
    WORKING_DIR,
    WORKSPACE_FILENAME,
    WorkingState,
    archive_stale,
    read_working_state,
    working_dir,
    workspace_path,
    write_working_state,
)

__all__ = [
    "EPISODIC_DIR",
    "LEARNINGS_FILENAME",
    "LESSONS_DIR",
    "LESSONS_JSONL",
    "LESSONS_MARKDOWN",
    "PREFERENCES_DIR",
    "PREFERENCES_FILENAME",
    "WORKING_DIR",
    "WORKSPACE_FILENAME",
    "AgentLearning",
    "EpisodicLog",
    "Lesson",
    "LessonsStore",
    "Preferences",
    "WorkingState",
    "archive_stale",
    "episodic_dir",
    "jsonl_path",
    "learnings_path",
    "lesson_id",
    "lessons_dir",
    "markdown_path",
    "preferences_dir",
    "preferences_path",
    "read_preferences",
    "read_working_state",
    "record_learning",
    "update_preferences",
    "working_dir",
    "workspace_path",
    "write_preferences",
    "write_working_state",
]
