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
HEARTBEAT_PATH = TMP_DIR / "live_watch_last.txt"
IDLE_STAMP_PATH = TMP_DIR / "live_watch_idle.txt"

LEAGUES = ("j1", "j2", "j3")
# 第36弾: ルヴァンカップ。fetch_match_events.py --league all がこれも取りに行くようになったので、
# 変化の判定にも含める。含めないと「得点/カード/交代に変化なし」と誤判定して、
# 取得はできているのにコミットされない(=画面に出ない)。
# live_matches() の方には入れていない。日程の持ち方が違う(leaguecup.json)ためで、
# 1時間ごとの拾い直しで取れる。試合中の5分間隔で追いたくなったら別途対応する。
EVENT_LEAGUES = LEAGUES + ("leaguecup",)
# このスクリプトが自動でコミットしてよい(=捨てても build_dist.py で作り直せる)ファイル。
# これ以外を触っているコミットは、人が書いたコードなので自動回復で捨ててはいけない。
GENERATED_PREFIXES = ("data/processed/", "data/history/", "dist/")
# ライブ扱いにする時間幅。index.html の LIVE_WINDOW_MINUTES と合わせてある
# (ハーフタイムと追加時間ぶんの余裕。延長のあるカップ戦は対象外)。
LIVE_WINDOW = timedelta(minutes=150)
# 前回の実行が固まったまま残った場合に備え、この時間を過ぎたロックは無視する
LOCK_STALE = timedelta(minutes=20)
LOG_MAX_BYTES = 512 * 1024
# 進行中の試合が無いときでも、この間隔で1回は取りに行く。
# 毎回取りに行かないのは、fetch_match_events.py が EVENTS_WINDOW_HOURS(36時間)以内の試合を
# すべて舐めるため。試合の翌日は24試合ぶんのリクエストになり、5分おきだと jleague.jp へ
# 1日約7000リクエストと過剰になる。1時間おきなら約600で、相手にも自分のPCにも無理がない。
# それでも拾いたいのは、試合後に遅れて公開されるハイライト動画(DAZN/J公式)と得点の訂正。
IDLE_INTERVAL = timedelta(hours=1)


def log(msg: str) -> None:
    line = f"[{datetime.now(JST):%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)


def heartbeat(live_count: int) -> None:
    """
    起動したこと自体を1行だけ残す(毎回上書き)。試合の無い時間帯はログに何も出さない設計なので、
    これが無いと「タスクスケジューラが動いていないのか、動いた上で何もしなかったのか」を
    区別できない。ログ本体に毎回書くと1日288行増えるため、別ファイルに最終状態だけ持つ。
    """
    try:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        # 中身はASCIIだけにする。Windowsで `type` すると CP932 として読まれるので、
        # UTF-8の日本語を書くと化けて読めなくなる(生存確認用のファイルなので確実に読めることを優先)。
        HEARTBEAT_PATH.write_text(
            f"{datetime.now(JST):%Y-%m-%d %H:%M:%S} JST  live={live_count}\n",
            encoding="utf-8",
        )
    except Exception:
        pass  # 記録の都合でバッチ本体を止めない


def idle_run_due(now: datetime) -> bool:
    """進行中の試合が無いときに、そろそろ1回取りに行くべきか。記録が無ければ行く。"""
    try:
        t = datetime.fromisoformat(IDLE_STAMP_PATH.read_text(encoding="utf-8").strip())
        return now - t >= IDLE_INTERVAL
    except Exception:
        return True


def mark_idle_run(now: datetime) -> None:
    """取りに行く直前に記録する(失敗しても次の1時間は間を空ける)。"""
    try:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        IDLE_STAMP_PATH.write_text(now.isoformat(), encoding="utf-8")
    except Exception:
        pass


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
    """
    python本体は sys.executable を使う(PATHのpythonが別のものを指していても壊れないように)。

    encoding/errors を明示するのが重要。text=True だけだとWindowsでは CP932 でデコードされるが、
    live_watch.bat が PYTHONIOENCODING=utf-8 を設定しているので子プロセスはUTF-8で書いてくる。
    食い違うとデコードに失敗し、stdoutがNoneのまま返ってきて
    "AttributeError: 'NoneType' object has no attribute 'strip'" で落ちる(2026-08-30に発生)。
    errors="replace" は、gitがCP932で出力する場合に備えた保険。
    """
    return subprocess.run(cmd, cwd=BASE_DIR, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return run(["git", *args], timeout=timeout)


def has_meaningful_changes() -> bool:
    """得点/カード/交代に実質的な変化があるか。終了コード0なら「あり」(workflowと同じ判定)。"""
    files = [str(PROCESSED_DIR / f"{lg}_match_events.json") for lg in EVENT_LEAGUES
             if (PROCESSED_DIR / f"{lg}_match_events.json").exists()]
    r = run([sys.executable, str(BASE_DIR / "scripts" / "git_diff_match_events.py"), *files])
    if (r.stdout or '').strip():
        log(r.stdout.strip()[:500])
    return r.returncode == 0


def merge_events(mine: dict, theirs: dict) -> dict:
    """同じ試合の詳細を2つ受け取り、情報量の多い方を採用して1つにまとめる。

    「新しい方を採る」ではなく「多い方を採る」のが肝。2026-08-30に、3分新しいというだけで
    ローカル側を採用した結果、向こうにしか無かったハイライト動画26件を消してしまった。
    新しい≠情報が多い。
    """
    out = dict(theirs)
    for key, ev in mine.items():
        base = theirs.get(key)
        if base is None:
            out[key] = ev  # 向こうに無い試合はそのまま足す
            continue
        merged = dict(base)
        # 得点/カード/交代は件数の多い方。速報中は増えていくだけで減ることはない
        for field in ("goals", "cards", "subs"):
            a, b = ev.get(field), base.get(field)
            if isinstance(a, list) and len(a) > len(b or []):
                merged[field] = a
        # 出場メンバーは中身が入っている方
        if not merged.get("lineups") and ev.get("lineups"):
            merged["lineups"] = ev["lineups"]
        # 動画IDは「片方にしか無い」なら有る方を採る。ここが26件を消した所
        for field in ("highlightVideoId", "daznVideoId"):
            if not merged.get(field) and ev.get(field):
                merged[field] = ev[field]
        # 検索の試行回数は多い方。巻き戻すと無駄な再検索でYouTube APIの枠を食う
        if (ev.get("daznSearchAttempts") or 0) > (merged.get("daznSearchAttempts") or 0):
            merged["daznSearchAttempts"] = ev.get("daznSearchAttempts")
            merged["daznLastSearchedAtJst"] = ev.get("daznLastSearchedAtJst")
        out[key] = merged
    return out


def _video_count(events: dict) -> int:
    return sum(1 for e in events.values() if e.get("highlightVideoId") or e.get("daznVideoId"))


def local_commits_to_replay() -> list[str] | None:
    """origin/main に無い手元のコミットのうち、生成物以外を触っているものを古い順に返す。

    生成物とコードを同じコミットに混ぜているものが1つでもあれば None を返す(回復を中止する合図)。
    そういうコミットは機械的に切り分けられないので、人が見るまで触らない方が安全。
    """
    r = git("rev-list", "--reverse", "origin/main..HEAD")
    out: list[str] = []
    for sha in (r.stdout or "").split():
        files = (git("show", "--pretty=", "--name-only", sha).stdout or "").split()
        gen = [f for f in files if f.startswith(GENERATED_PREFIXES)]
        other = [f for f in files if not f.startswith(GENERATED_PREFIXES)]
        if gen and other:
            log(f"[error] コミット {sha[:8]} が生成物とコードを同時に含んでいる。自動回復はしない")
            return None
        if other:
            out.append(sha)
    return out


def sync_with_origin() -> None:
    """取得を始める前に origin/main へ追いつく(第30弾で順序を入れ替えた)。

    以前は build_dist -> commit -> pull --rebase の順だった。この順だと dist/ を先にコミットして
    しまうので、Actions側が同じ時間帯に書いた dist/ics/*.ics や dist/deploy-time.txt /
    dist/deploy-version.txt と必ずぶつかる。どれも「中身が毎回変わる生成物」なので、
    両者が同時に走ればコンフリクトは避けられない。実際 2026-08-30 と 08-31 の二度、
    ここで rebase が詰まって push が半日〜1日止まった。

    先に pull しておけば dist/ は origin の最新の上で作り直されるので、ぶつかりようがない。
    ついでに data/processed/*_matches.json も最新になるため、
    「前日の試合が status=NS のまま凍りつき、試合終了後にしか走らないハイライト動画の検索が
    止まる」という二次被害(2026-08-31に発生)も同時に防げる。

    失敗しても止めない。取得自体はできるし、後段の commit_and_push がリトライと自動回復を持つ。
    """
    r = git("pull", "--rebase", "--autostash", "origin", "main", timeout=180)
    if r.returncode != 0:
        log(f"[warn] 事前のpullに失敗(取得は続ける): {((r.stderr or '') + (r.stdout or '')).strip()[:200]}")
        git("rebase", "--abort")


def recover_from_stuck_rebase() -> bool:
    """pull --rebase が3回とも失敗したときの最後の手段。origin/main に合わせ直す。

    なぜ要るか(2026-08-31に判明):
        8/30の夜から10回連続でpushできなくなっていた。dist/ の生成物(.icsのDTSTAMPなど)が
        Actions側の「データ更新」コミットとぶつかり、rebaseが毎回同じコミットで止まっていた。
        --abort して次回に賭ける作りなので、次回も同じ所で止まる = 永久に詰まる。
        さらに悪いことに、pullが通らないので data/processed/*_matches.json が古いまま凍りつき、
        前日の試合を「まだ終わっていない(status=NS)」と誤認して、
        試合終了後にしか走らないハイライト動画の検索まで止まっていた。
        push詰まりが、静かにデータの中身まで壊す。だから自動で抜け出せるようにする。

    やること:
        手元のコミットを捨てて origin/main に合わせ直し、捨てる前の match_events.json だけを
        情報量の多い方で拾い直して1コミットにまとめる。dist/ は build_dist.py で作り直せるので
        取っておく必要がない。

    安全弁:
        このスクリプトの管轄外(index.html など)に未コミットの変更が残っている間は何もしない。
        編集中に reset --hard を撃つと、その作業ごと消えてしまう。
    """
    # "??"(未追跡)は除く。git reset --hard は未追跡ファイルに触らないので、
    # 置いてあっても危険が無い。ここを除外し忘れていたせいで、docs/ に指示書を1つ置いただけで
    # 自動回復が止まり、pushが8時間止まった(2026-08-31)。安全弁は追跡中の変更にだけ効かせる。
    leftover = [ln for ln in (git("status", "--porcelain").stdout or "").splitlines()
                if ln.strip() and not ln.startswith("??")]
    if leftover:
        log("[error] pushできず。追跡中のファイルに未コミットの変更があるので自動回復は見送る: "
            + ", ".join(leftover)[:200])
        return False

    # 捨ててよいのは生成物だけのコミット。コード変更(scripts/やindex.html、docs/)を含むコミットは
    # reset --hard で消えてしまうので、いったん退避して origin の上に戻す。
    replay = local_commits_to_replay()
    if replay is None:
        return False

    if git("fetch", "origin", "main", timeout=180).returncode != 0:
        log("[error] fetchできなかった。回復は次回に持ち越す")
        return False

    mine = {}
    for lg in LEAGUES:
        path = PROCESSED_DIR / f"{lg}_match_events.json"
        try:
            mine[lg] = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # 読めないなら触らない方が安全
            log(f"[error] {path.name} を読めなかったので回復を中止: {exc}")
            return False

    log("[warn] rebaseが詰まっている。origin/main に合わせ直す(手元のコミットは捨てる)")
    if git("reset", "--hard", "origin/main", timeout=180).returncode != 0:
        log("[error] reset --hard に失敗した")
        return False

    for sha in replay:
        r = git("cherry-pick", sha, timeout=120)
        if r.returncode != 0:
            git("cherry-pick", "--abort")
            log(f"[error] コミット {sha[:8]} を origin の上に戻せなかった。回復を中止する")
            return False
        log(f"[info] コミット {sha[:8]} を origin の上に戻した")

    for lg in LEAGUES:
        path = PROCESSED_DIR / f"{lg}_match_events.json"
        theirs = json.loads(path.read_text(encoding="utf-8"))
        before = _video_count(theirs.get("events", {}))
        theirs["events"] = merge_events(mine[lg].get("events", {}), theirs.get("events", {}))
        after = _video_count(theirs["events"])
        path.write_text(json.dumps(theirs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"[info] {lg}: 試合{len(theirs['events'])}件に統合(動画あり {before}件 -> {after}件)")

    if run([sys.executable, str(BASE_DIR / "scripts" / "build_dist.py")]).returncode != 0:
        log("[error] 回復中の build_dist に失敗した")
        return False

    git("add", "-A", "data/processed", "dist", "data/history")
    if git("diff", "--cached", "--quiet").returncode == 0:
        log("回復完了。origin/main との差は無くなったので押すものは無い")
        return True
    stamp = f"{datetime.now(JST):%Y-%m-%d %H:%M}"
    if git("commit", "-m", f"auto: 試合詳細速報更新(ローカル・再構成) {stamp} JST").returncode != 0:
        log("[error] 回復後のcommitに失敗した")
        return False
    r = git("push", "origin", "main", timeout=180)
    if r.returncode == 0:
        log("回復してpushできた")
        return True
    log(f"[error] 回復後もpushできなかった: {((r.stderr or '') + (r.stdout or '')).strip()[:300]}")
    return False


def unpushed_count() -> int:
    """origin/main に無い手元のコミット数。事前の pull で origin/main は最新になっている前提。"""
    out = (git("rev-list", "--count", "origin/main..HEAD").stdout or "").strip()
    return int(out) if out.isdigit() else 0


def commit_and_push() -> bool:
    stamp = f"{datetime.now(JST):%Y-%m-%d %H:%M}"
    # data/history も必ず含める。build_dist.py は build_ics 経由で
    # data/history/ics_state.json(.icsのSEQUENCE永続化用)を書き換えるため、ここに入れないと
    # 未ステージのまま残り、直後の git pull --rebase が
    # "cannot pull with rebase: You have unstaged changes" で即座に失敗する。
    # Actionsは毎回クリーンなcheckoutなので表面化しない、ローカル実行に固有の落とし穴。
    git("add", "-A", "data/processed", "dist", "data/history")
    if git("diff", "--cached", "--quiet").returncode == 0:
        # 第30弾: ステージが空でも、前回押せずに残ったコミットがあることがある。
        # ここで素通りすると、データに変化の無い日が続くかぎり永久に押されない
        # (2026-08-31、手で直したコミットが宙に浮いた)。
        ahead = unpushed_count()
        if ahead == 0:
            log("ステージに何も無い。コミットはしない")
            return True
        log(f"新しい変更は無いが、未pushのコミットが{ahead}件ある。押すところまでやる")
    else:
        r = git("commit", "-m", f"auto: 試合詳細速報更新(ローカル) {stamp} JST")
        if r.returncode != 0:
            log(f"[error] commit失敗: {(r.stderr or '').strip()[:300]}")
            return False
    # Actions(update.yml)や手元の作業とpushが競合する前提で、必ずrebaseしてから押す。
    # 失敗の中身を必ずログに残すこと。理由が出ないと、通信の問題なのか作業ツリーの問題なのか
    # 切り分けられない(実際それで一度詰まった)。
    for i in (1, 2, 3):
        # --autostash が要る。このスクリプトは5分おきに走るので、利用者が index.html などを
        # 編集している最中に動くことがある。未コミットの変更が1つでもあると git は rebase を
        # 拒否し("cannot pull with rebase: You have unstaged changes")、pushまで到達しない。
        # --autostash なら自動で退避して rebase 後に戻すので、編集中でも巻き込まない。
        r = git("pull", "--rebase", "--autostash", "origin", "main", timeout=180)
        if r.returncode != 0:
            log(f"[warn] pull失敗 ({i}/3): {((r.stderr or '') + (r.stdout or '')).strip()[:300]}")
            # コンフリクトでrebaseが中断したまま残ると、次回以降の実行が全部失敗し続ける。
            # 5分おきに走るスクリプトなので、途中状態を残さず必ず元に戻す
            # (取り込めなかった変更は手元のコミットとして残るので、次回また試される)。
            git("rebase", "--abort")
        else:
            r = git("push", "origin", "main", timeout=180)
            if r.returncode == 0:
                log(f"push成功 (試行{i}回目)")
                return True
            log(f"[warn] push失敗 ({i}/3): {((r.stderr or '') + (r.stdout or '')).strip()[:300]}")
        if i < 3:
            time.sleep(5)  # 競合の解消を待つ。即座に3回叩いても意味がない
    # 3回とも駄目だった。--abort して次回に賭けるだけだと、同じ所で止まり続けて永久に詰まる。
    return recover_from_stuck_rebase()


def main() -> None:
    rotate_log()
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    force = "--force" in args
    now = datetime.now(JST)

    live = live_matches(now)
    heartbeat(len(live))
    idle_due = (not live) and idle_run_due(now)
    if not live and not idle_due and not force:
        # 試合が無く、前回の拾い直しから1時間も経っていない。ログも残さず終わる
        # (5分おきに起動されるので、ここで毎回書くと1日に何百行も増える)。
        return

    if not acquire_lock():
        log("前回の実行がまだ動いている。今回はスキップする")
        return

    try:
        if live:
            log(f"進行中の試合 {len(live)}件: " + ", ".join(f"{m['home']}-{m['away']}" for m in live[:4]))
        else:
            log("進行中の試合なし。1時間ごとの拾い直し(ハイライト動画・得点の訂正)")
            mark_idle_run(now)
        sync_with_origin()  # 第30弾: 取得の前に origin へ追いつく(dist/のコンフリクトを構造的に無くす)
        r = run([sys.executable, str(BASE_DIR / "scripts" / "fetch_match_events.py"), "--league", "all"])
        if r.returncode != 0:
            log(f"[error] fetch_match_events 失敗: {((r.stderr or '') + (r.stdout or '')).strip()[-500:]}")
            return
        if not has_meaningful_changes():
            # 変化が無くても、押し損ねたコミットが残っているなら押しておく(第30弾)。
            if unpushed_count() > 0:
                log("得点/カード/交代に変化なし。ただし未pushのコミットがあるので押す")
                commit_and_push()
            else:
                log("得点/カード/交代に変化なし。build_dist もコミットもしない")
            return
        if dry_run:
            log("--dry-run なのでここで終了(変化は検出済み)")
            return
        r = run([sys.executable, str(BASE_DIR / "scripts" / "build_dist.py")])
        if r.returncode != 0:
            log(f"[error] build_dist 失敗: {((r.stderr or '') + (r.stdout or '')).strip()[-500:]}")
            return
        commit_and_push()
    finally:
        release_lock()


if __name__ == "__main__":
    main()
