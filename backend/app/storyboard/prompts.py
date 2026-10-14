"""The preview and queued generation share one character-conditioned prompt."""
from app.auth.dependencies import scoped_get
from app.models import Character, CharacterVersion


def build_prompt(db, scope, shot):
    anchors, negatives, versions = [], [], []
    for cid in shot.character_ids:
        character = scoped_get(db, Character, cid, scope)
        if character.active_version_id:
            version = scoped_get(db, CharacterVersion, character.active_version_id, scope)
            dna = version.dna
            anchors.append(f"{character.name}: {dna.get('prompt_anchor', '')}; face {dna.get('face', '')}; hair {dna.get('hair', '')}; costume {dna.get('costume', '')}; style {dna.get('style', '')}")
            negatives.append(dna.get("negative_prompt", ""))
            versions.append({"character_id": character.id, "version_id": version.id, "dna": dict(dna)})
    return {"prompt": ". ".join(filter(None, [shot.description, shot.action, shot.prompt, *anchors])), "negative_prompt": ", ".join(filter(None, [shot.negative_prompt, *negatives])), "character_versions": versions}
