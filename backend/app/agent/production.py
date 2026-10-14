"""Structured first-stage production planning for the Director."""

import re
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record
from app.auth.dependencies import ProjectScope
from app.config import settings
from app.generation.service import request_generation
from app.models import (
    Character,
    CharacterVersion,
    Episode,
    GenerationJob,
    OutboxEvent,
    Scene,
    Shot,
    StorySource,
)
from app.providers.llm import DashScopeLLMProvider


class ProductionOptions(BaseModel):
    max_episodes: int = Field(default=1, ge=1, le=5)
    max_scenes: int = Field(default=8, ge=1, le=30)
    max_shots: int = Field(default=12, ge=1, le=60)
    max_duration_seconds: float = Field(default=120, gt=0, le=900)
    auto_generate_images: bool = True
    max_quality_retries: int = Field(default=2, ge=0, le=3)


class CharacterPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=120)
    background: str = Field(default="", max_length=4000)
    face: str = Field(default="", max_length=500)
    hair: str = Field(default="", max_length=500)
    costume: str = Field(default="", max_length=500)
    style: str = Field(default="", max_length=500)
    voice: str = Field(default="", max_length=500)
    prompt_anchor: str = Field(default="", max_length=1000)


class ShotPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str = Field(min_length=1, max_length=4000)
    action: str = Field(default="", max_length=2000)
    dialogue: str = Field(default="", max_length=2000)
    emotion: str = Field(default="", max_length=80)
    shot_type: str = Field(default="medium", max_length=60)
    camera_angle: str = Field(default="eye-level", max_length=60)
    camera_movement: str = Field(default="static", max_length=60)
    duration: float = Field(default=6, ge=1, le=30)
    character_names: list[str] = Field(default_factory=list, max_length=10)
    prompt: str = Field(default="", max_length=4000)
    negative_prompt: str = Field(default="", max_length=2000)


class ScenePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    heading: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=3000)
    shots: list[ShotPlan] = Field(min_length=1, max_length=30)


class EpisodePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=200)
    synopsis: str = Field(default="", max_length=3000)
    scenes: list[ScenePlan] = Field(min_length=1, max_length=30)


class ProductionPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    characters: list[CharacterPlan] = Field(min_length=1, max_length=30)
    episodes: list[EpisodePlan] = Field(min_length=1, max_length=5)

    @property
    def scenes(self) -> Iterable[ScenePlan]:
        return (scene for episode in self.episodes for scene in episode.scenes)

    @property
    def shots(self) -> Iterable[ShotPlan]:
        return (shot for scene in self.scenes for shot in scene.shots)


def plan_story(story: str, request: str, options: ProductionOptions) -> ProductionPlan:
    if settings.director_llm_mode == "fake":
        return fake_plan(story, options)
    # Keep the first phase bounded; later phases can summarize arbitrarily long novels in chunks.
    planning_story = story[:120000]
    prompt = f"""
Create a production plan for an animated drama from the story below.
Request: {request[:2000]}
Limits: at most {options.max_episodes} episode(s), {options.max_scenes} scene(s), {options.max_shots} shot(s), and {options.max_duration_seconds:g} total seconds.
Return one JSON object with exactly these top-level keys: characters, episodes.
Each character must have name, background, face, hair, costume, style, voice, prompt_anchor.
Each episode has title, synopsis, scenes. Each scene has heading, description, shots.
Each shot has description, action, dialogue, emotion, shot_type, camera_angle, camera_movement, duration, character_names, prompt, negative_prompt.
Use character_names that exactly match characters.name. Keep the number of shots within the limit. Make prompts concrete and production-ready. Do not add markdown or commentary.

Story:
{planning_story}
""".strip()
    plan = ProductionPlan.model_validate(DashScopeLLMProvider().complete_json(prompt))
    scene_count = sum(1 for _ in plan.scenes)
    shot_count = sum(1 for _ in plan.shots)
    duration = sum(shot.duration for shot in plan.shots)
    if len(plan.episodes) > options.max_episodes or scene_count > options.max_scenes or shot_count > options.max_shots:
        raise ValueError("Director plan exceeds the requested production limits")
    if duration > options.max_duration_seconds:
        raise ValueError("Director plan exceeds the requested duration limit")
    known = {character.name for character in plan.characters}
    unknown = sorted({name for shot in plan.shots for name in shot.character_names if name not in known})
    if unknown:
        raise ValueError(f"Director plan references unknown characters: {', '.join(unknown[:5])}")
    return plan


def fake_plan(story: str, options: ProductionOptions) -> ProductionPlan:
    """Deterministic, bounded plan for local workflow verification, not AI output."""
    sentences = [text.strip() for text in re.split(r"(?<=[。！？.!?])\s*|\n+", story) if text.strip()]
    count = min(options.max_shots, 30, len(sentences), int(options.max_duration_seconds))
    if count < 1:
        raise ValueError("A production needs at least one second and one non-empty shot")
    duration = min(3.0, options.max_duration_seconds / count)
    shots = [ShotPlan(description=text[:4000], dialogue=text[:2000], duration=duration, character_names=["主角"]) for text in sentences[:count]]
    return ProductionPlan(
        characters=[CharacterPlan(name="主角", background="本地流程验证角色", prompt_anchor="consistent main character")],
        episodes=[EpisodePlan(title="流程验证剧集", scenes=[ScenePlan(heading="故事场景", shots=shots)])],
    )


def create_production(
    db: Session,
    scope: ProjectScope,
    run_id: str,
    trace_id: str,
    story_title: str,
    story_content: str,
    plan: ProductionPlan,
    options: ProductionOptions,
) -> dict:
    story = StorySource(workspace_id=scope.workspace_id, project_id=scope.project_id, title=story_title, content=story_content, created_by=scope.user_id)
    db.add(story)
    db.flush()

    characters: dict[str, Character] = {}
    for item in plan.characters:
        character = Character(workspace_id=scope.workspace_id, project_id=scope.project_id, name=item.name, version=1)
        db.add(character)
        db.flush()
        version = CharacterVersion(
            workspace_id=scope.workspace_id,
            project_id=scope.project_id,
            character_id=character.id,
            version_no=1,
            status="ACTIVE",
            dna={"face": item.face, "hair": item.hair, "costume": item.costume, "style": item.style, "voice": item.voice, "prompt_anchor": item.prompt_anchor},
            background=item.background,
            created_by=scope.user_id,
        )
        db.add(version)
        db.flush()
        character.active_version_id = version.id
        characters[item.name] = character

    episodes_created = 0
    scenes_created = 0
    shots_created = 0
    jobs = []
    shot_ids = []
    existing_episode_no = db.scalar(select(func.max(Episode.episode_no)).where(Episode.workspace_id == scope.workspace_id, Episode.project_id == scope.project_id)) or 0
    for episode_offset, episode_plan in enumerate(plan.episodes, start=1):
        episode_no = int(existing_episode_no) + episode_offset
        episode = Episode(workspace_id=scope.workspace_id, project_id=scope.project_id, episode_no=episode_no, title=episode_plan.title, synopsis=episode_plan.synopsis)
        db.add(episode)
        db.flush()
        episodes_created += 1
        for scene_no, scene_plan in enumerate(episode_plan.scenes, start=1):
            scene = Scene(workspace_id=scope.workspace_id, project_id=scope.project_id, episode_id=episode.id, scene_no=scene_no, heading=scene_plan.heading, description=scene_plan.description)
            db.add(scene)
            db.flush()
            scenes_created += 1
            for shot_no, shot_plan in enumerate(scene_plan.shots, start=1):
                character_ids = [characters[name].id for name in shot_plan.character_names]
                shot = Shot(
                    workspace_id=scope.workspace_id,
                    project_id=scope.project_id,
                    episode_id=episode.id,
                    scene_id=scene.id,
                    shot_no=shot_no,
                    shot_type=shot_plan.shot_type,
                    camera_angle=shot_plan.camera_angle,
                    camera_movement=shot_plan.camera_movement,
                    duration=shot_plan.duration,
                    description=shot_plan.description,
                    action=shot_plan.action,
                    dialogue=shot_plan.dialogue,
                    emotion=shot_plan.emotion,
                    character_ids=character_ids,
                    prompt=shot_plan.prompt,
                    negative_prompt=shot_plan.negative_prompt,
                    status="STORYBOARD_READY",
                )
                db.add(shot)
                db.flush()
                shots_created += 1
                shot_ids.append(shot.id)
                if options.auto_generate_images:
                    jobs.append(request_generation(db, scope, shot.id, "IMAGE", f"autoproduce:{run_id}:image:{shot.id}", trace_id, agent_run_id=run_id))

    db.flush()
    record(db, actor_type="AGENT", actor_id=run_id, action="production.structure.create", resource_type="story", resource_id=story.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id, agent_run_id=run_id, safe_summary=f"created {len(characters)} characters, {episodes_created} episodes, {scenes_created} scenes, {shots_created} shots")
    return {"story_id": story.id, "characters": len(characters), "episodes": episodes_created, "scenes": scenes_created, "shots": shots_created, "shot_ids": shot_ids, "image_jobs": [job.id for job in jobs]}


def initialize_run(db, run, scope, planner=None):
    """Commit this result together with the generated structure and its outbox."""
    inputs = run.production_json["input"]
    options = ProductionOptions.model_validate(inputs["options"])
    plan = (planner or plan_story)(inputs["story_content"], run.request, options)
    summary = create_production(db, scope, run.id, run.trace_id, inputs["story_title"], inputs["story_content"], plan, options)
    run.status = "RUNNING" if summary["image_jobs"] else "COMPLETED"
    run.current_node = "Image Generation Queue" if summary["image_jobs"] else "Structure Ready"
    run.decision_summary = f"已完成自动拆解：{summary['characters']} 个角色、{summary['episodes']} 集、{summary['scenes']} 个场景、{summary['shots']} 个镜头；已排队 {len(summary['image_jobs'])} 个图像任务。"
    run.warnings = ["当前为测试模式：计划由规则生成，用于验证制作流程，不代表真实 AI 创作效果。"] if settings.director_llm_mode == "fake" else []
    run.production_json = {"mode": "AUTOPRODUCE", "stage": "MEDIA_QUEUED" if summary["image_jobs"] else "STRUCTURE_READY", "unattended": inputs["unattended"], "options": options.model_dump(), "story_id": summary["story_id"], "shot_ids": summary["shot_ids"], "created": {key: value for key, value in summary.items() if key not in {"story_id", "image_jobs", "shot_ids"}}, "image_job_ids": summary["image_jobs"], "plan": plan.model_dump(), "knowledge_index_status": "QUEUED"}
    run.estimated_cost = sum(job.estimated_cost for job in db.scalars(select(GenerationJob).where(GenerationJob.id.in_(summary["image_jobs"]))).all()) if summary["image_jobs"] else 0
    db.add(OutboxEvent(workspace_id=run.workspace_id, project_id=run.project_id, topic="knowledge.index", aggregate_id=summary["story_id"], payload={"run_id": run.id, "story_id": summary["story_id"]}, deduplication_key=f"production:index:{run.id}"))
