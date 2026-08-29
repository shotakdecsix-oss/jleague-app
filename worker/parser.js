/*
 * jleague.jp の試合ページから得点・カード・交代・出場メンバーを抜き出すパーサ。
 * scripts/match_events_parser.py の移植版(ネットワークアクセスなし・純粋関数のみ)。
 *
 * Python版と同じ正規表現・同じ手順にしてある。移植の正しさは worker/verify_parser.js が
 * data/tmp/sample_match_livetxt.html に対して Python版の出力と1バイト単位で突き合わせて検証する。
 * 片方だけ直すと壊れるので、正規表現を変えるときは必ず両方を直して検証を通すこと。
 *
 * Pythonとの主な書き換え:
 *   (?P<name>...) -> (?<name>...) / re.S -> s フラグ / finditer -> matchAll(gフラグ必須)
 */

const NEXT_F_RE = /self\.__next_f\.push\(\[1,(".*?")\]\)<\/script>/gs;
const CHUNK_LINE_RE = /^([0-9a-f]+):(.*)$/;

/* Next.jsのRSCストリーミングペイロードを結合し、チャンクID -> 生JSON文字列 のMapにする。
   Pythonのdictと違い、JSのプレーンなObjectは "1" や "2" のような整数に見えるキーを
   数値順に並べ替えてしまう。チャンクIDは16進数なので、まさにその条件を踏む。
   得点や交代は出現順がそのまま時系列の並びになるため、挿入順が保証されるMapを使う。 */
export function extractNextChunks(html) {
  const parts = [];
  for (const m of html.matchAll(NEXT_F_RE)) parts.push(JSON.parse(m[1]));
  const full = parts.join("");
  const chunks = new Map();
  for (const line of full.split("\n")) {
    const m = CHUNK_LINE_RE.exec(line);
    if (m) chunks.set(m[1], m[2]);
  }
  return chunks;
}

const GOAL_RE = /"div","(?<minute>[0-9+]+)",\{.*?"\$L\w+",null,\{"club":\{"name":"(?<club>[^"]+)".*?"player":\{"name":"(?<player>[^"]+)","position":"(?<position>[^"]+)".*?"children":\["GOAL!"," "\]\}\](?:,\["\$","span",null,\{[^}]*"children":"(?<score>\d+-\d+)")?/gs;

const CARD_RE = /"cardType":"(?<type>yellow|red)","playerName":"(?<player>[^"]+)","playerPosition":"(?<position>[^"]+)".*?"teamName":"(?<club>[^"]+)"/g;
const CARD_MINUTE_RE = /widget-container-(?<minute>[0-9+]+)'/;

const SUB_BLOCK_RE = /"\$1","substitution-\d+-(?<club>[^"]+)"/;
const SUB_MINUTE_RE = /widget-container-(?<minute>[0-9+]+)'/;
const SUB_ITEM_RE = /"variant":"(?<variant>in|out)".*?"children":\["(?<pos1>[A-Z]+)"," ","(?<pos2>\d+)"\]\}\],\["\$","p",null,\{"className":"[^"]*item-details--name"[^}]*"children":"(?<name>[^"]+)"/gs;

export function findGoals(chunks) {
  const out = [];
  for (const v of chunks.values()) {
    if (!v.includes("GOAL!")) continue;
    for (const m of v.matchAll(GOAL_RE)) {
      const g = m.groups;
      out.push({
        minute: g.minute,
        club: g.club,
        player: g.player,
        position: g.position,
        // Python版はマッチしなかった任意グループをNoneにする。JSのundefinedはJSONで消えるのでnullに寄せる。
        scoreAfter: g.score === undefined ? null : g.score,
      });
    }
  }
  return out;
}

export function findCards(chunks) {
  const out = [];
  for (const v of chunks.values()) {
    const mMin = CARD_MINUTE_RE.exec(v);
    for (const m of v.matchAll(CARD_RE)) {
      const g = m.groups;
      out.push({
        minute: mMin ? mMin.groups.minute : null,
        type: g.type,
        player: g.player,
        position: g.position,
        club: g.club,
      });
    }
  }
  return out;
}

export function findSubs(chunks) {
  const out = [];
  for (const v of chunks.values()) {
    if (!v.includes("選手交代")) continue;
    const items = [...v.matchAll(SUB_ITEM_RE)];
    // 記事本文などでの"選手交代"という単語の単純ヒット。実データが無ければ捨てる
    if (items.length === 0) continue;
    const mClub = SUB_BLOCK_RE.exec(v);
    const mMin = SUB_MINUTE_RE.exec(v);
    out.push({
      minute: mMin ? mMin.groups.minute : null,
      club: mClub ? mClub.groups.club : null,
      items: items.map(it => ({
        variant: it.groups.variant,
        position: it.groups.pos1 + " " + it.groups.pos2,
        name: it.groups.name,
      })),
    });
  }
  return out;
}

/* s[start]が'['または'{'である前提で、対応する閉じ括弧の位置を返す(文字列リテラル内は無視)。 */
function extractBalanced(s, start) {
  const openCh = s[start];
  const closeCh = openCh === "[" ? "]" : "}";
  let depth = 0, i = start, inStr = false, esc = false;
  while (i < s.length) {
    const c = s[i];
    if (inStr) {
      if (esc) esc = false;
      else if (c === "\\") esc = true;
      else if (c === '"') inStr = false;
    } else {
      if (c === '"') inStr = true;
      else if (c === openCh) depth++;
      else if (c === closeCh) { depth--; if (depth === 0) return i; }
    }
    i++;
  }
  return -1;
}

/* 出場メンバー(スタメン)。"formations":[...] は整形済みJSONがそのまま埋まっているので、
   文字列パターンではなく括弧の対応を辿って切り出し、JSON.parseする。 */
export function findFormations(chunks) {
  const marker = '"formations":[';
  for (const v of chunks.values()) {
    const idx = v.indexOf(marker);
    if (idx === -1) continue;
    const start = idx + '"formations":'.length;
    const end = extractBalanced(v, start);
    if (end === -1) continue;
    try { return JSON.parse(v.slice(start, end + 1)); } catch (e) { continue; }
  }
  return null;
}

const LINEUP_MEMBER_RE = new RegExp(
  '"legacyPlayerPhotoLookup":\\{"seasonYear":\\d+,"teamNameKey":"(?<teamSlug>[a-z0-9]+)","playerId":"(?<pid>\\d+)"[^}]*\\}\\}\\],' +
  '\\["\\$","div",null,\\{"className":"m-lineup-list-item__content","children":\\[' +
  '\\["\\$","p",null,\\{"className":"[^"]*m-lineup-list-item__position","ref":"\\$undefined","children":"(?<posnum>[^"]+)"\\}\\],' +
  '\\["\\$","div",null,\\{"className":"m-lineup-list-item__main-content","children":\\[' +
  '\\["\\$","p",null,\\{"className":"[^"]*m-lineup-list-item__name","ref":"\\$undefined","children":"(?<name>[^"]+)"',
  "g"
);

/* 控えメンバー用。個別試合ページのトップに スタメン+控え が一括で描画されている。
   戻り値: {teamSlug: [{id,number,position,name}, ...出現順]} */
export function findLineupMembers(chunks) {
  const text = [...chunks.values()].join("");
  const byTeam = {};
  for (const m of text.matchAll(LINEUP_MEMBER_RE)) {
    const g = m.groups;
    const parts = g.posnum.split(" ");
    const position = parts.length ? parts[0] : null;
    const number = parts.length > 1 ? parts.slice(1).join(" ") : null;
    (byTeam[g.teamSlug] = byTeam[g.teamSlug] || []).push({
      id: g.pid, number, position, name: g.name,
    });
  }
  return byTeam;
}

const HIGHLIGHT_VIDEO_RE = /"videoSrc":"(?<url>https:\/\/www\.youtube\.com\/watch\?v=(?<vid>[A-Za-z0-9_-]+))"/;

/* ハイライト動画のvideoId。試合結果ページ(review/)にのみ埋め込まれる。無ければnull。 */
export function findHighlightVideoId(chunks) {
  const text = [...chunks.values()].join("");
  const m = HIGHLIGHT_VIDEO_RE.exec(text);
  return m ? m.groups.vid : null;
}
