"""Creating the config for a new surah by copying an existing one."""

from __future__ import annotations

import pytest
import yaml

from app.services import scaffold
from app.utils.logging import QVGError


@pytest.fixture
def made(project, tmp_path):
    """Surah 112's config, written from the project's own config.yaml."""
    destination = tmp_path / "config.al-ikhlas.yaml"
    scaffold.create_config(
        surah_number=112,
        english_name="Al-Ikhlas",
        arabic_name="الإخلاص",
        urdu_name="سورۃ الاخلاص",
        template=project / "config.yaml",
        destination=destination,
    )
    return destination


def test_it_points_at_the_new_surah(made):
    data = yaml.safe_load(made.read_text(encoding="utf-8"))
    assert data["project"]["surah"] == 112
    assert data["project"]["slug"] == "112_Al-Ikhlas"
    assert data["paths"]["data_file"] == "data/al_ikhlas.json"
    assert data["word_timings"]["file"] == "data/word_timings_112.json"


def test_each_surah_gets_its_own_audio_directory(made):
    """The files inside are numbered 1..n for THAT surah, so they cannot share."""
    data = yaml.safe_load(made.read_text(encoding="utf-8"))
    assert data["paths"]["recitation_dir"] == "assets/audio/recitation/112"


def test_the_download_url_uses_the_mushaf_wide_number(made):
    """The CDN indexes 1-6236, not 1-n within the surah."""
    data = yaml.safe_load(made.read_text(encoding="utf-8"))
    assert "{global_ayah}" in data["audio_sources"]["recitation_url_template"]


def test_the_shared_settings_are_carried_over_untouched(project, made):
    """Only the surah's own values change; the design is common to all of them."""
    template = yaml.safe_load((project / "config.yaml").read_text(encoding="utf-8"))
    data = yaml.safe_load(made.read_text(encoding="utf-8"))
    assert data["theme"] == template["theme"]
    assert data["animation"] == template["animation"]
    assert data["video"] == template["video"]


def test_the_comments_survive(made):
    """They are most of what that file is; a YAML round-trip would lose them."""
    text = made.read_text(encoding="utf-8")
    assert text.count("#") > 100
    assert "Surah Al-Ikhlas (112)" in text
    # ... and the header says where the recitation CDN's numbering comes in.
    assert "6222-6225" in text


def test_it_refuses_to_overwrite_without_being_asked(project, made):
    with pytest.raises(QVGError, match="already exists"):
        scaffold.create_config(
            surah_number=112, english_name="Al-Ikhlas", arabic_name="x",
            urdu_name="y", template=project / "config.yaml", destination=made,
        )

    scaffold.create_config(
        surah_number=112, english_name="Al-Ikhlas", arabic_name="x",
        urdu_name="y", template=project / "config.yaml", destination=made,
        force=True,
    )


def test_an_impossible_surah_number_is_refused(project, tmp_path):
    with pytest.raises(QVGError):
        scaffold.create_config(
            surah_number=115, english_name="Nope", arabic_name="x", urdu_name="y",
            template=project / "config.yaml", destination=tmp_path / "no.yaml",
        )
