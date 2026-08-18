"""Transcription options actually reach the model.

The async wrapper once accepted `vad_filter` and silently dropped it on the way
to `transcribe_sync`. Nothing failed: the stage recorded `vad_filter: false` in
its metrics — computed from settings, not from what was passed — while the
model ran with VAD on. A job that reports one thing and does another is worse
than one that simply errors, so the forwarding is pinned here.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.pipeline import transcribe as module


class TestForwarding:
    async def test_async_wrapper_forwards_every_option(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        def fake_sync(audio_path, **kwargs):
            captured.update(kwargs)
            return module.Transcript(
                segments=[], language="en", language_probability=1.0,
                duration_s=1.0, model="stub",
            )

        monkeypatch.setattr(module, "transcribe_sync", fake_sync)

        await module.transcribe(
            Path("audio.wav"),
            model_name="tiny",
            device="cpu",
            language="en",
            beam_size=3,
            vad_filter=False,
            vad_threshold=0.25,
        )

        assert captured["vad_filter"] is False
        assert captured["vad_threshold"] == 0.25
        assert captured["model_name"] == "tiny"
        assert captured["language"] == "en"
        assert captured["beam_size"] == 3

    def test_signatures_agree(self) -> None:
        """Any option on the wrapper must exist on the worker it delegates to.

        This is the check that would have caught the dropped argument: the two
        signatures drifting apart is exactly how it happened.
        """
        wrapper = set(inspect.signature(module.transcribe).parameters)
        worker = set(inspect.signature(module.transcribe_sync).parameters)
        assert wrapper <= worker, f"wrapper accepts options the worker lacks: {wrapper - worker}"


class TestVadOptions:
    def test_threshold_only_sent_when_filtering(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """faster-whisper rejects vad_parameters when the filter is off."""
        seen: dict = {}

        class FakeModel:
            def transcribe(self, _path, **kwargs):
                seen.update(kwargs)
                info = type(
                    "Info", (), {"language": "en", "language_probability": 1.0, "duration": 1.0}
                )()
                return iter([]), info

        monkeypatch.setattr(module, "_get_model", lambda *_a, **_k: (FakeModel(), "cpu", "int8"))

        module.transcribe_sync(Path("a.wav"), vad_filter=False)
        assert seen["vad_filter"] is False
        assert "vad_parameters" not in seen

        seen.clear()
        module.transcribe_sync(Path("a.wav"), vad_filter=True, vad_threshold=0.3)
        assert seen["vad_parameters"] == {"threshold": 0.3}


class TestRepetitionFilter:
    """Verbatim repeats of the previous line are decoder loops, not speech.

    This replaced a `no_speech_prob` threshold that was **unsound**. That score
    is relative to decoding conditions rather than an absolute measure of
    quality: validated against a clean corpus transcribed with VAD on it never
    exceeded 0.125, but with VAD off genuine dialogue routinely scored 0.44 —
    *higher* than the 0.30 hallucination it was meant to catch. It deleted five
    real lines from one clip and reduced another to zero segments.
    """

    @staticmethod
    def _model(monkeypatch: pytest.MonkeyPatch, texts: list[str], probs: list[float] | None = None):
        probs = probs or [0.01] * len(texts)

        class FakeSeg:
            def __init__(self, i: int, text: str, nsp: float) -> None:
                self.start, self.end = float(i), float(i + 1)
                self.text = f" {text} "
                self.avg_logprob, self.no_speech_prob, self.compression_ratio = -0.2, nsp, 1.2

        class FakeModel:
            def transcribe(self, _path, **_kwargs):
                info = type(
                    "Info", (), {"language": "en", "language_probability": 1.0, "duration": 10.0}
                )()
                return iter(
                    [FakeSeg(i, t, p) for i, (t, p) in enumerate(zip(texts, probs, strict=True))]
                ), info

        monkeypatch.setattr(module, "_get_model", lambda *_a, **_k: (FakeModel(), "cpu", "int8"))

    def test_drops_a_verbatim_repeat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._model(monkeypatch, ["You're insane!", "You're insane!"])
        result = module.transcribe_sync(Path("a.wav"))

        assert [s.text for s in result.segments] == ["You're insane!"]
        assert result.dropped_segments == 1

    def test_ignores_case_and_punctuation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._model(
            monkeypatch,
            ["All teams, the daughter is secured.", "All teams the daughter is secured!"],
        )
        assert len(module.transcribe_sync(Path("a.wav")).segments) == 1

    def test_keeps_a_line_that_recurs_later(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only *consecutive* repeats are loops. A phrase said twice in a
        conversation is normal speech."""
        self._model(monkeypatch, ["Yes.", "Then you know what I want.", "Yes."])
        assert len(module.transcribe_sync(Path("a.wav")).segments) == 3

    def test_never_drops_on_no_speech_prob(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The regression this filter replaced.

        With VAD off, genuine dialogue scores ~0.44 and a hallucination ~0.30 —
        the signal is inverted, so it must not be used as a gate.
        """
        self._model(
            monkeypatch,
            ["Mr. President, we have to get you out of here!", "Where is my daughter?"],
            probs=[0.44, 0.70],
        )
        result = module.transcribe_sync(Path("a.wav"))

        assert len(result.segments) == 2, "high no_speech_prob must not discard real speech"
        assert result.dropped_segments == 0


class TestVocabulary:
    def test_sent_as_hotwords(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not `initial_prompt`.

        Measured: an initial prompt combined with condition_on_previous_text
        made the model transcribe the prompt itself — the tail came back as
        "Harkov, Vorshevsky, Modern Warfare".
        """
        seen: dict = {}

        class FakeModel:
            def transcribe(self, _path, **kwargs):
                seen.update(kwargs)
                info = type(
                    "Info", (), {"language": "en", "language_probability": 1.0, "duration": 1.0}
                )()
                return iter([]), info

        monkeypatch.setattr(module, "_get_model", lambda *_a, **_k: (FakeModel(), "cpu", "int8"))

        module.transcribe_sync(Path("a.wav"), vocabulary="Makarov, Harkov")
        assert seen["hotwords"] == "Makarov, Harkov"
        assert "initial_prompt" not in seen

    def test_absent_when_not_given(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict = {}

        class FakeModel:
            def transcribe(self, _path, **kwargs):
                seen.update(kwargs)
                info = type(
                    "Info", (), {"language": "en", "language_probability": 1.0, "duration": 1.0}
                )()
                return iter([]), info

        monkeypatch.setattr(module, "_get_model", lambda *_a, **_k: (FakeModel(), "cpu", "int8"))

        module.transcribe_sync(Path("a.wav"), vocabulary="")
        assert "hotwords" not in seen
