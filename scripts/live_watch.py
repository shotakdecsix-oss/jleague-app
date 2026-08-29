"""
進行中の試合の得点者・カード・交代・出場メンバーを取りに行く、ローカルPC常駐用のスクリプト。
Windowsのタスクスケジューラから5分おきに呼ばれる前提(live_watch.bat 経由)。

なぜローカルPCから叩くのか:
    .github/workflows/match_events.yml は5分おきのcronで書いてあるが、実測すると
    GitHub側にスケジュールを大幅に間引かれ、実際の起動は6〜11時間に1回しかなかった
    (2026-08-29にActionsの実行履歴で確認。失敗ではなく、そもそも起動していない)。
    cronの頻度を下げても、毎時0分を避けて分をずらしても改善しなかった。
    Cloudflare Workersに移す案も検討したが、無料枠のCPU時間が10msなのに対して
    1試合ぶんのHTML(1.7MB)のパースに21msかかるため成立しなかった。
    結局「確実に5分おきに起動できる場所」が手元のPCしか無かった、という経緯。

やること:
    1. 進行中の試合(キックオフ済み・150分以内・未終了)があるか判定する
    2. 無ければ即終了する。gitもRenderも動かさない(試合の無い日は毎回ここで終わる)
    3. あれば fetch_match_events.py を実行して取得する
    4. 得点/カード/交代に実質的な変化があるときだけ build_dist.py -> commit -> push する

    4の判定は git_diff_match_events.py に任せている。generatedAtJst のような
    毎回変わるだけの値は無視されるので、無駄なコミットとRenderの再デプロイが起きない。

CLI:
    python scripts/live_watch.py            通常実行
    python scripts/live_watch.py --dry-run  取得まで行い、コミットとpushはしない
    python scripts/live_watch.py --force    進行中の試合が無くても取得する(動作確認用)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_utils import JST  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"
TMP_DIR = BASE_DIR / "data" / "tmp"
LOCK_PATH = TMP_DIR / "live_watch.lock"
LOG_PATH = TMP_DIR / "live_watch.log"

LEAGUES = ("j1", "j2", "j3")
# ライブ扱いにする時間幅。index.html の LIVE_WINDOW_MINUTES と合わせてある
# (ハーフタイムと追加時間ぶんの余裕。延長のあるカップ戦は対象外)。
LIVE_WINDOW = timedelta(minutes=150)
# 前回の実行が固まったまま残った場合に備え、この時間を過ぎたロックは無視する
LOCK_STALE = timedelta(minutes=20)
LOG_MAX_BYTES = 512 * 1024


def log(msg: str) -> None:
    line = f"[{datetime.now(JST):%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)


def rotate_log() -> None:
    """タスクスケジューラから毎日叩かれるのでログが際限なく伸びる。一定サイズで切り詰める。"""
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            tail = LOG_PATH.read_text(encoding="utf-8", errors="replace")[-LOG_MAX_BYTES // 2:]
            LOG_PATH.write_text(tail, encoding="utf-8")
    except Exception:
        pass  # ログの都合でバッチ本体を止めない


def live_matches(now: datetime) -> list[dict]:
    """キックオフ済みで150分以内、かつ未終了の試合。"""
    out = []
    for league in LEAGUES:
        path = PROCESSED_DIR / f"{league}_matches.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log(f"[warn] {path.name} を読めない: {e}")
            continue
        for m in data.get("matches", []):
            if m.get("finished"):
                continue
            ko = m.get("kickoffJst")
            if not ko:
                continue
            try:
                t = datetime.fromisoformat(ko)
            except ValueError:
                continue
            if t <= now < t + LIVE_WINDOW:
                out.append({"league": league, "home": m["home"]["ja"], "away": m["away"]["ja"]})
    return out


def acquire_lock() -> bool:
    """
    5分おきに起動されるので、前回が終わっていないことがある(取得は数十秒〜数分かかる)。
    多重実行するとgitの操作が競合するため、単純なロックファイルで直列化する。
    """
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        age = datetime.now(JST) - datetime.fromtimestamp(LOCK_PATH.stat().st_mtime, JST)
        if age < LOCK_STALE:
            return False
        log(f"[warn] 古いロック({int(age.total_seconds() // 60)}分前)を無視して続行する")
    LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    """python本体は sys.executable を使う(PATHのpythonが別のものを指していても壊れないように)。"""
    return subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, timeout=timeout)


def git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return run(["git", *args], timeout=timeout)


def has_meaningful_changes() -> bool:
    """得点/カード/交代に実質的な変化があるか。終了コード0なら「あり」(workflowと同じ判定)。"""
    files = [str(PROCESSED_DIR / f"{lg}_match_events.json") for lg in LEAGUES]
    r = run([sys.executable, str(BASE_DIR / "scripts" / "git_diff_match_events.py"), *files])
    if r.stdout.strip():
        log(r.stdout.strip()[:500])
    return r.returncode == 0


def commit_and_push() -> bool:
    stamp = f"{datetime.now(JST):%Y-%m-%d %H:%M}"
    # data/history も必ず含める。build_dist.py は build_ics 経由で
    # data/history/ics_state.json(.icsのSEQUENCE永続化用)を書き換えるため、ここに入れないと
    # 未ステージのまま残り、直後の git pull --rebase が
    # "cannot pull with rebase: You have unstaged changes" で即座に失敗する。
    # Actionsは毎回クリーンなcheckoutなので表面化しない、ローカル実行に固有の落とし穴。
    git("add", "-A", "data/processed", "dist", "data/history")
    if git("diff", "--cached", "--quiet").returncode == 0:
        log("ステージに何も無い。コミットはしない")
        return True
    r = git("commit", "-m", f"auto: 試合詳細速報更新(ローカル) {stamp} JST")
    if r.returncode != 0:
        log(f"[error] commit失敗: {r.stderr.strip()[:300]}")
        return False
    # Actions(update.yml)や手元の作業とpushが競合する前提で、必ずrebaseしてから押す。
    # 失敗の中身を必ずログに残すこと。理由が出ないと、通信の問題なのか作業ツリーの問題なのか
    # 切り分けられない(実際それで一度詰まった)。
    for i in (1, 2, 3):
        r = git("pull", "--rebase", "origin", "main", timeout=180)
        if r.returncode != 0:
            log(f"[warn] pull失敗 ({i}/3): {(r.stderr or r.stdout).strip()[:300]}")
        else:
            r = git("push", "origin", "main", timeout=180)
            if r.returncode == 0:
                log(f"push成功 (試行{i}回目)")
                return True
            log(f"[warn] push失敗 ({i}/3): {(r.stderr or r.stdout).strip()[:300]}")
        if i < 3:
            time.sleep(5)  # 競合の解消を待つ。即座に3回叩いても意味がない
    log("[error] pushできなかった。次回の実行で再試行される")
    return False


def main() -> None:
    rotate_log()
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    force = "--force" in args
    now = datetime.now(JST)

    live = live_matches(now)
    if not live and not force:
        # 試合の無い時間帯はここで終わる。ログも残さない(1日に何百行も増えるため)
        return

    if not acquire_lock():
        log("前回の実行がまだ動いている。今回はスキップする")
        return

    try:
        log(f"進行中の試合 {len(live)}件: " + ", ".join(f"{m['home']}-{m['away']}" for m in live[:4]))
        r = run([sys.executable, str(BASE_DIR / "scripts" / "fetch_match_events.py"), "--league", "all"])
        if r.returncode != 0:
            log(f"[error] fetch_match_events 失敗: {(r.stderr or r.stdout).strip()[-500:]}")
            return
        if not has_meaningful_changes():
            log("得点/カード/交代に変化なし。build_dist もコミットもしない")
            return
        if dry_run:
            log("--dry-run なのでここで終了(変化は検出済み)")
            return
        r = run([sys.executable, str(BASE_DIR / "scripts" / "build_dist.py")])
        if r.returncode != 0:
            log(f"[error] build_dist 失敗: {(r.stderr or r.stdout).strip()[-500:]}")
            return
        commit_and_push()
    finally:
        release_lock()


if __name__ == "__main__":
    main()
