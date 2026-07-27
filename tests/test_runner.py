import argparse
import asyncio
from pathlib import Path

import pytest

from university_news.pipeline.runner import (
    build_command,
    failed_stages,
    run_all,
    run_site,
    select_failed_runs,
    select_sites,
)


def test_build_command_for_site() -> None:
    command = build_command(
        "usp",
        python_executable="/tmp/python",
        stage="all",
        extractor="trafilatura",
        output_root=Path("data/sites"),
        concurrency=4,
        force=True,
        verbose=True,
    )

    assert command == [
        "/tmp/python",
        "-m",
        "university_news",
        "usp",
        "--stage",
        "all",
        "--extractor",
        "trafilatura",
        "--output",
        "data/sites/usp",
        "--concurrency",
        "4",
        "--force",
        "--verbose",
    ]


async def test_run_all_executes_selected_sites_and_records_failures(tmp_path) -> None:
    class FakeProcess:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

        async def wait(self) -> int:
            return self.returncode

    calls: list[tuple[str, ...]] = []

    async def fake_subprocess(*command, stdout, stderr):
        del stdout, stderr
        calls.append(command)
        site = command[3]
        await asyncio.sleep(0)
        return FakeProcess(1 if site == "ufal" else 0)

    args = argparse.Namespace(
        sites=["usp", "ufal"],
        stage="discovery",
        extractor="trafilatura",
        output_root=tmp_path / "sites",
        jobs=2,
        concurrency=3,
        force=False,
        verbose=False,
        no_live_output=True,
        progress_interval=0,
        failed_only=False,
    )

    results = await run_all(
        args,
        subprocess_factory=fake_subprocess,
        python_executable="/tmp/python",
    )

    assert sorted(call[3] for call in calls) == ["ufal", "usp"]
    assert {result.site: result.returncode for result in results} == {
        "ufal": 1,
        "usp": 0,
    }
    assert (tmp_path / "logs" / "usp.log").is_file()
    assert (tmp_path / "logs" / "ufal.log").is_file()


async def test_run_all_rejects_invalid_jobs(tmp_path) -> None:
    args = argparse.Namespace(
        sites=["usp"],
        stage="all",
        extractor="trafilatura",
        output_root=tmp_path / "sites",
        jobs=0,
        concurrency=5,
        force=False,
        verbose=False,
        no_live_output=True,
        progress_interval=0,
        failed_only=False,
    )

    with pytest.raises(ValueError, match="--jobs"):
        await run_all(args)


def test_select_sites_can_filter_to_failed_only(tmp_path) -> None:
    root = tmp_path / "sites"
    (root / "usp").mkdir(parents=True)
    (root / "ufal").mkdir()
    (root / "uem").mkdir()
    (root / "usp" / "errors.jsonl").write_text("", encoding="utf-8")
    (root / "ufal" / "errors.jsonl").write_text(
        '{"stage":"discovery"}\n',
        encoding="utf-8",
    )

    assert select_sites(
        ["usp", "ufal", "uem"],
        output_root=root,
        failed_only=True,
    ) == ["ufal"]


def test_failed_only_selects_the_failed_stage(tmp_path) -> None:
    root = tmp_path / "sites"
    (root / "ufal").mkdir(parents=True)
    (root / "ufla").mkdir()
    (root / "usp").mkdir()
    (root / "ufal" / "errors.jsonl").write_text(
        '{"stage":"discovery"}\n', encoding="utf-8"
    )
    (root / "ufla" / "errors.jsonl").write_text(
        '{"stage":"fetching"}\n', encoding="utf-8"
    )
    (root / "usp" / "errors.jsonl").write_text(
        '{"stage":"extraction"}\n', encoding="utf-8"
    )

    assert select_failed_runs(
        ["ufal", "ufla", "usp"], output_root=root, requested_stage="all"
    ) == [("ufal", "all"), ("ufla", "fetching"), ("usp", "extraction")]


def test_failed_stages_falls_back_to_log_summary(tmp_path) -> None:
    root = tmp_path / "sites"
    root.mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "ufal.log").write_text(
        '2026 INFO {"discovery_errors": 0, "fetch_errors": 2, '
        '"extraction_errors": 0}\n',
        encoding="utf-8",
    )

    assert failed_stages("ufal", output_root=root) == {"fetching"}


async def test_run_all_skips_when_failed_only_finds_no_sites(tmp_path, capsys) -> None:
    args = argparse.Namespace(
        sites=["usp"],
        stage="all",
        extractor="trafilatura",
        output_root=tmp_path / "sites",
        jobs=1,
        concurrency=5,
        force=False,
        verbose=False,
        no_live_output=True,
        progress_interval=0,
        failed_only=True,
    )

    results = await run_all(args)

    assert results == []
    assert "Nenhum site" in capsys.readouterr().out


async def test_run_site_streams_output_to_log_and_terminal(tmp_path, capsys) -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(b"linha 1\n")
            self.stdout.feed_data(b"linha 2\n")
            self.stdout.feed_eof()

        async def wait(self) -> int:
            return self.returncode

    async def fake_subprocess(*command, stdout, stderr):
        del command, stdout, stderr
        return FakeProcess()

    log_path = tmp_path / "usp.log"
    result = await run_site(
        "usp",
        ["/tmp/python", "-m", "university_news", "usp"],
        log_path,
        asyncio.Semaphore(1),
        fake_subprocess,
        live_output=True,
    )

    terminal = capsys.readouterr().out
    assert result.returncode == 0
    assert "[usp] linha 1" in terminal
    assert "linha 1\nlinha 2\n" in log_path.read_text(encoding="utf-8")
