# AutoLineDigitizer × Starrydata3 — 引き継ぎ報告書 / Handover Report

**For:** Tomoya Mato (NIMS) — engineer taking over / continuing development
**From:** Aditya Acharya (IIT-BHU, visiting NIMS)
**Date:** 2026-08-06 (last day in Japan)
**Repos:**
- `github.com/adityaaa-IIT-BHU/starrydata3` — branch `main`, HEAD `c4b4d2c`
- `github.com/adityaaa-IIT-BHU/AutoLineDigitizer` — branch `feat/pdf-figure-gallery`, HEAD `95936f8` (fork of `t29mato/AutoLineDigitizer`)

> この文書は **日本語（第I部）** と **English（Part II）** の両方で同じ内容を記載しています。
> コマンド・ファイルパス・数値は両方で共通です。
>
> This document contains the same content in **Japanese (Part I)** and **English (Part II)**.
> Commands, file paths and numbers are identical in both.

---
---

# 第 I 部 — 日本語

## 0. 3分でわかる全体像

論文PDF → 図の数値データ → スキーマ検証済みデータベース、これを**すべてローカルの1台のノートPC上で**行うパイプラインです。構成は2つのリポジトリだけです。

| | 役割 | 技術 |
|---|---|---|
| **AutoLineDigitizer (ALD)** | デスクトップアプリ。PDFから図を発見 → 軸較正 → 曲線抽出 → NCMRDメタデータ抽出 → **人間による承認ゲート** | Python 3.11 + Flet、ローカルML（LineFormer / ChartDete / EasyOCR / PP-DocLayoutV2） |
| **Starrydata3 (SD3)** | 自己ホスト型データベースサービス。NCMRDレコードの検証・保存・語彙正規化・単位変換・横断クエリ・Web UI | FastAPI + SQLModel + SQLite、ML依存なし |

**設計三原則**（これは仕様であり、変更する場合は意図的に行ってください）:

1. **ローカリティ** — 推論はすべてローカルCPU/GPUで実行。VLM補助のみ任意でリモートAPI。無効化すれば何もマシンから出ません。
2. **語彙の誠実さ (Vocabulary honesty)** — 公式NCMRD語彙に一致しない物性名は**そのまま保存して⚠フラグを立てる**。決して勝手にリネームしません。
3. **非破壊的取り込み (Non-destructive ingestion)** — DOIでupsertしますが、メタデータのない「digitizationスタブ」は既存レコードに**マージ**され、構造的に上書きできません。

**責務分離（2026-08-03のユーザ決定・重要）**: 図の**編集作業はALDデスクトップアプリ側**で行います。SD3のWebポータルは「キュー管理・NCMRD閲覧・検証・ブラウズ・軽微な修正」用です。Web上に本格的な図エディタを作るのは断念しました（後述 §4.1）。

---

## 1. 起動手順（再起動後のランブック）

### 1.1 Starrydata3 サーバ（必須）

```bash
cd /Users/Shared/starrydata3
.venv/bin/uvicorn starrydata3.app:app --host 127.0.0.1 --port 8300
```
→ http://localhost:8300 、API docs は `/docs`。

初回セットアップ（新しいマシンの場合）:
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m starrydata3.manage initdb
.venv/bin/python -m starrydata3.manage seedprops          # NCMRD語彙 248語 + 拡張16語
.venv/bin/python -m starrydata3.manage createkey me --scope write   # APIキーが1度だけ表示される
```

⚠ **落とし穴**: uvicorn 実行中に `starrydata3.db` を削除・再作成すると、サーバは**削除済みのinodeを掴んだまま**動き続けます。`initdb` 後は必ず uvicorn を再起動してください。

### 1.2 ローカルLLM トンネル（NCMRD抽出をGPUボックスで行う場合）

GPUボックス (`starryai`) のollamaはlocalhost限定なので、自己回復するSSHトンネルが必要です:
```bash
while true; do ssh -N -L 11434:localhost:11434 box; sleep 3; done
```
（一度切れると抽出が無言でハングします。必ずループで回してください。`pkill -f 'ssh.*-L 11434'` で再起動。）

### 1.3 Assist サイドカー（Webポータルの図ツールを使う場合のみ）

```bash
# alddev conda環境で
cd /Users/Shared/AutoLineDigitizer && python src/assist_server.py     # :8390
```
SD3 は `/api/v1/assist/{op}` でこれをプロキシします（`SD3_ASSIST_URL` で変更可）。SD3本体はML非依存を保つための構成です。サイドカーが起動していない場合、SD3は 503 と起動方法を返します。

### 1.4 ALD デスクトップアプリ

```bash
conda activate alddev
cd /Users/Shared/AutoLineDigitizer && python src/desktop_app.py
# LANでWebサービスとして公開する場合:
ALD_WEB=1 ALD_WEB_PORT=8550 python src/desktop_app.py
```

### 1.5 ポート一覧

| ポート | サービス |
|---|---|
| 8300 | Starrydata3 (uvicorn) |
| 8390 | ALD assist サイドカー |
| 8550 | ALD Web モード (flet web) |
| 11434 | ollama（GPUボックスへのSSHトンネル） |

**Webポータル用の書き込みキーは平文で** `/Users/Shared/starrydata3/.portal.key` にあります（gitignore済み）。scratchpad は再起動で消えるので、恒久的な秘密情報は絶対に置かないでください。

---

## 2. 現在**動作している**もの（2026-08-06 時点で検証済み）

### 2.1 Starrydata3 — データベースサービス

テストスイート: **34件すべてパス** (`.venv/bin/python -m pytest tests/ -q`)。
`tests/test_app.py`（コア）、`tests/test_tier1.py`（品質機能）、`tests/test_tier2.py`（組成・キュー・ファセット）。

現在のDB内容 (`starrydata3.db`):

| テーブル | 件数 | 内容 |
|---|---|---|
| `record` | 3 | エポキシ(60001) / FeAlSi熱電(60002) / 電池(60003) |
| `graphcurve` | 52 | デジタイズ済み曲線インデックス |
| `pointvalue` | 64 | 表・物性ツリーからの単一測定値 |
| `property` | 275 | NCMRD公式語彙 + ローカル拡張 |
| `reviewsession` / `reviewfigure` | 10 / 184 | バッチ抽出済みのレビュー待ち論文 |
| `qualityflag` | 4 | 整合性チェックの結果 |
| `apikey` | 4 | SHA-256ハッシュのみ保存 |

**動作している機能:**

- **スキーマ検証** — NCMRD (KMDS) JSON Schema 2020-12。公式スキーマに対する意図的な緩和は2点のみ、実行時変換として実装（`app.py:52` `ncmrd_schema()`）: (a) `digitization_data` 拡張フィールドの注入、(b) ローカル識別子のformatパターン緩和。`POST /api/v1/validate` で保存せずに検証可能。
- **「まるごと保存・インデックスは再生成」** — NCMRDレコードのJSONを verbatim 保存し、取り込みのたびに曲線インデックスと点値インデックスを削除・再生成 (`app.py:632` `store_record`)。正規化ロジックを改善したら再取り込みするだけで過去データにも遡及適用されます（`manage.py reindex`）。
- **語彙正規化** (`app.py:314` `match_property`) — 保守的なカスケード: 正規化後の完全一致 → 検証済みシノニム表 → 特殊ケース（ΔT、単位付きの裸のT）→ 4文字超で候補が唯一の場合のみ部分文字列一致。一致しないものは verbatim 保存 + フラグ。
- **単位正準化** (`units.py`) — SI接頭辞（µ/μ/u の異体字含む）、先頭の10のべき乗（"10⁴ S/m"）、分離接頭辞（"mu V"）、'm'がメートルと milli の両方である曖昧性の解決。**同一の基本単位シグネチャ間でのみ**変換します（V K⁻¹ → µV K⁻¹ は可、物理量をまたぐ強制変換は不可）。°C→K のみ加算オフセットとして特別扱い。生の値と正準値の両方を保存。
- **スタブマージガード** (`app.py:768` `_digitization_stub` / `app.py:798` `merge_digitized`) — メタデータなしのデジタイズのみアップロードを構造的に検知し、既存レコードにマージ。**これは実際に起きた事故の構造的修正です**（一度リッチなFeAlSiレコードが破壊されました）。回帰テストで固定済み。
- **整合性チェック** (`checks.py`) — PF=S²σ、ZT=S²σT/κ、σ は 1/ρ からフォールバック。独自のSI単位パーサ（Ω⁻¹↔S、cm、mu接頭辞に対応）。×10ⁿ の単位誤りや比のドリフト（＝軸較正ミスの兆候）を検出。取り込み時に自動実行、`manage.py recheck` で再実行。
- **コーパス分位帯** (`app.py:1601` `property_band`) — 物性ペアごとの p10/p50/p90 帯を全曲線から計算し、レコードページのプロットの背後に描画。外れ値が一目でわかります。
- **プロヴェナンスカプセル** (`app.py:1523`) — 図のクロップPNG + 較正 + 抽出器 + キュレータ名をレコードに紐付け。ALDがSD3アップロード成功後に自動送信します。
- **組成パーサ** (`composition.py`) — 化学式 → 正規化された `comp_key` + 元素リスト（Fe3Al2Si3 → Al0.25Fe0.375Si0.375）。`/materials` と `/material/{key}` の物質ドシエを支えます。
- **Web UI** — `/`（検索可能な論文一覧）、`/view/{sid}`（レコード詳細）、`/ncmrd/{sid}`（完全なNCMRD編集ビューア）、`/explore`（横断的な物性ペアプロット）、`/materials`、`/find`（条件付き範囲検索）、`/queue`（論文キュー）、`/review`（レビューポータル）。
- **エクスポート** — `/ui/export.json`（完全バックアップ、再アップロードで復元可能）、`/ui/export.csv`（ロングフォーマット、全曲線点+全測定値）。

### 2.2 AutoLineDigitizer — 取得とキュレーション

- **図の発見** — 4つの戦略: PP-DocLayoutV2 (MinerU、既定・chartクラスがあり多パネル分割可)、DocLayout-YOLO、VLMスクリーナ、PyMuPDFの埋め込みラスタ直接抽出。
- **軸較正** — ChartDete が18クラスのチャート要素を検出 → EasyOCR（数字allowlist + 4通りの前処理アンサンブル）→ **RANSAC** による頑健回帰。線形/対数の両方を試し、対数は「より当てはまりが良く、かつ1.5decade以上またぐ」場合のみ採用。
- **曲線抽出** — LineFormer（Mask2Former + Swin-T、100 queries、512×512、閾値0.3）。各マスクを列ごとの中央値行に縮約して系列化。電池向けfinetune版も選択可能。
- **散布点検出** (`marker_extractor.py`, v0.8.0で全面再構築) — 色レイヤごとのアーキテクチャ（Voronoi LAB層、ハロー吸収）、塗りつぶし/中抜き判定、Scatteract式テンプレート増幅、系列を**色と形状の両方**で分割。回帰スイート `tools/test_marker_extractor.py` は5種のチャートクラスで全パス。
- **NCMRDメタデータ抽出** (`ncmrd_parallel.py`, 1905行) — 2フェーズ・セクション並列。第1フェーズで publication / sample / material の実体を安定IDとともに確立し、第2フェーズで図・表・本文パッセージがそのIDを相互参照する形で抽出。結果はPDFの隣にキャッシュされ再オープン時に再利用されます。
- **人間による承認ゲート** — 図ごとに独立した2つのチェックボックス *Axes OK* / *Extraction OK*。両方チェックされた図のみアップロード対象。軸名・単位の編集、凡例による曲線リネーム、曲線削除、図の除外、ルーペ下での点の追加・消去が可能。
- **VLM補助（任意、非上書きポリシー）** — 5種類: 軸読み取り、較正検証、凡例ラベリング、語彙正準化、抽出監査。**自動提案はフィールドが空か前回の自動値のままの場合のみ書き込まれ、キュレータの手編集は決して上書きされません。** キーがなければすべて決定的なローカルフォールバックに縮退します。
- **バッチモード** (`src/batch_run.py`) — 無人で「フォルダ内の全PDF」を処理:
  ```bash
  python src/batch_run.py papers/ [--push http://localhost:8300 --push-key sd3_…]
                                  [--model general_v2] [--detector mineru] [--limit N] [--no-ncmrd] [--force]
  ```
  PDFごとに `{stem}_batch/digitizations.json`（ピクセル空間の系列 + 軸設定）を保存。**失敗は図単位・論文単位で閉じ込められ、バッチ全体を止めません。** 再実行時は既存の状態をスキップします。デスクトップアプリはPDFを開いたときにこの状態を自動ロードするので、サムネイルをクリックすると再デジタイズせずに復元されます。
- **ローカルNCMRD抽出（GPUボックス）** — `model="local:<ollama-model>"`（`KMDS_MODEL` 環境変数）。2論文評価済み（下記 §2.4）。

### 2.3 レビューポータル（SD3側）

バッチランナーが `POST /api/v1/review/sessions` にNCMRDレコード + 図PNG + デジタイズ状態を投げ込み → `/review` で一覧、`/review/{sid}` でキュレーションします。図ごとの承認・却下・編集、NCMRD JSONのライブ検証つき編集、そして **Approve** で `_px_to_data`（対数対応）によりピクセル→データ変換して本番DBへ取り込み + プロヴェナンス添付 + 整合性チェックまで一気通貫。ラウンドトリップはテスト済みです。

**現在10セッション・184図が `pending` のまま**キューに積まれています（下記 §5.1）。

### 2.4 評価済みの数値（論文に記載済み・再現可能）

**抽出精度**（人手デジタイズ済みの100図ベンチマーク、31本の熱電論文、700曲線、約14,700点。Starrydata2キュレータがWPDで作成したもの）:

| 指標 | 全100図 | 信頼できる82図 |
|---|---|---|
| Task 6a 曲線再現率（平均/中央値） | 0.85 / 0.93 | 0.88 / 0.94 |
| Task 6b データ抽出スコア | 0.63 / 0.70 | 0.71 / 0.75 |
| 曲線同定精度 | 88% | 92% |
| 点誤差（y軸レンジ比、中央値） | 2.7% | 2.7% |
| 軸レンジ誤差 x/y（中央値） | 1.3% / 2.0% | 1.3% / 1.7% |

**重要な洞察**: ピクセル空間で再スコアすると点誤差の中央値は 2.4% になります。つまり**残差の大半は曲線トレースではなく軸較正に起因します**。100図中99図で較正は生成されましたが、8図で致命的に誤っていました（ティックラベルの誤読、偽の対数軸検出）。これこそが *Axes OK* チェックポイントが人間の前に置かれている理由です。

**スループット**（Apple silicon CPU、GPUなし）: モデルロード約4秒（一度きり）、図あたり中央値10秒（軸解析が約9秒で支配的、LineFormerは1秒未満）、平均19秒。図発見は短い論文で約10秒、メタデータ抽出は4ページのレターで約45秒。**短い論文の自動処理部分は2〜3分で完了します。**

**ローカルLLM vs Claude**（ポリマー論文、未知ドメイン）: ローカル 0違反 / 根拠性83% / 5.3分 vs Claude 2違反 / 根拠性42% / 同程度の時間。ローカルモデルはNCMRD抽出で Claude と同等かそれ以上です。

---

## 3. アーキテクチャ地図（どこに何があるか）

### Starrydata3 (`/Users/Shared/starrydata3`)

| ファイル | 行数 | 内容 |
|---|---|---|
| `starrydata3/app.py` | 1770 | API本体・データモデル・取り込みロジック |
| `starrydata3/web.py` | 1199 | Web UIの全ページ（HTMLはPythonの文字列内） |
| `starrydata3/checks.py` | 245 | 物性間の整合性チェック |
| `starrydata3/units.py` | 105 | 単位正準化 |
| `starrydata3/composition.py` | 101 | 化学式パーサ |
| `starrydata3/manage.py` | 266 | CLI: `initdb` `seedprops` `createkey` `listkeys` `reindex` `recheck` `addpapers` `release` |
| `starrydata3/ncmrd_viewer.html` | — | 完全なNCMRD編集ビューア（ALDから移植・自動ロード注入済み） |
| `starrydata3/digitizer/` | — | StarryDigitizer v1.13.3 のビルド済みdist（`/digitizer` で配信） |
| `ncmrd_schema.json` | 1.43MB | NCMRD v15.2.4 スキーマ |
| `ncmrd_vocab_extensions.json` | — | ローカル拡張16語（**ALD/src のコピーと同期必須**） |

**主要な関数の入り口** — 変更するときはここから:

- `app.py:85` `validate_ncmrd()` — スキーマ検証
- `app.py:314` `match_property()` — 語彙マッチングのカスケード
- `app.py:408` `extract_curves()` — 曲線インデックスの再生成 + ドナーマッチング
- `app.py:528` `extract_point_values()` — 表・物性ツリーからの点値インデックス
- `app.py:632` `store_record()` — 取り込みの本体
- `app.py:768` `_digitization_stub()` / `app.py:798` `merge_digitized()` — スタブマージガード
- `app.py:1230–1491` — レビューポータルのAPI（push / patch / approve）
- `app.py:1281` `assist()` — ALDサイドカーへのプロキシ
- `app.py:1494` `_px_to_data()` — ピクセル→データ変換（対数対応）

### AutoLineDigitizer (`/Users/Shared/AutoLineDigitizer/src`)

| ファイル | 行数 | 内容 |
|---|---|---|
| `desktop_app.py` | 5185 | Fletデスクトップアプリ本体（**巨大**。分割は将来の課題） |
| `ncmrd_parallel.py` | 1905 | 2フェーズNCMRD抽出 |
| `mineru_layout/pp_doclayoutv2.py` | 1528 | レイアウト検出 |
| `marker_extractor.py` | 814 | 散布点検出 |
| `chartdete_infer.py` | 711 | チャート要素検出 |
| `smart_axis_extractor.py` | 643 | 軸較正（RANSAC） |
| `pdf_figures.py` | 602 | 図の発見 |
| `vlm_verifier.py` | 540 | VLM補助 |
| `batch_run.py` | 360 | バッチランナー |
| `ncmrd_vocab.py` | 203 | 語彙マッチング（SD3側と対になる実装） |
| `llm_backend.py` | 203 | バックエンド選択（Claude / ローカルollama） |
| `assist_server.py` | 176 | 計算サイドカー (:8390) |
| `starrydata3_client.py` | 73 | SD3へのアップロード + プロヴェナンス送信 |
| `tools/starrydata_upload.py` | — | **Starrydata2** へのアップロード（未コミット・§4.3参照） |

### データモデルの不変条件（壊さないでください）

1. **NCMRDレコードのJSONが真実。それ以外はすべて再生成可能なインデックス。** 新しいクエリ機能を追加するときは、レコードを変形するのではなく、新しいインデックスを派生させてください。
2. **語彙に一致しない物性名は verbatim + フラグ。** 「近そうな語」にマッピングしてはいけません。
3. **単位変換は同一基本単位シグネチャ間のみ。** 物理量をまたぐ変換は絶対にしないでください。
4. **メタデータのないアップロードは既存レコードを上書きできない。**
5. **ピクセル空間のままの系列はデータとしてインデックスしない**（偽データを作らないため）。

---

## 4. 中途半端／保留中のもの（正直に）

### 4.1 StarryDigitizer のWeb埋め込み — **保留（PARKED）**

`starry-digitizer` v1.13.3 (t29mato, MIT) をクローンしてviteでビルドし、`/digitizer` で配信・レビューページに埋め込むところまでは出来ています。画像の自動ロードブリッジはChromeで動作します。

**未完成**: SD側のストアへの曲線・軸データの注入。`reactive()` でのラップ、`App.vue` のnative-propパッチ（"no data()" で失敗）を試しましたが完了していません。SDのデータセットをセッション状態に書き戻す postMessage も未実装です。

**2026-08-03のユーザ判断**: 「編集はALDアプリでやって、SD3にpushする。このWebエディタの開発は難しい」——**この方向でパイプラインは完結しています**。SD埋め込みは明示的に要望がない限り再開しないでください。Mato-sanがSDの内部構造をよく知っているので、もし再開するなら注入ポイントは `src/application/services/canvasHandler` と `datasetRepository` です（Piniaではなくシングルトンストアのようです）。

### 4.2 GPUボックスへのWebサービス展開 — **Mac側完了・ボックス側未着手**

`ALD_WEB=1 python src/desktop_app.py` で flet を 0.0.0.0 で公開するところまではMac側で検証済み（HTTP 200確認。playwrightでの本文取得は空でしたが、fletはクライアント側描画なのでこれは想定内です）。

**ボックス側インストールは未実施**（イーサネットアダプタが5回目の故障で作業できず）。手順:
```bash
ssh box
conda create -n ald python=3.11 && conda activate ald
export https_proxy=http://proxyout.nims.go.jp:8888
pip install torch  # CUDA版
pip install mmcv-full==1.7.2   # 長時間のコンパイル
pip install -r requirements-dev.txt
git clone --recursive -b feat/pdf-figure-gallery <repo>   # プロキシ経由
# Macから重みをscp（LAN経由なので高速）:
#   ~/AutoLineDigitizer/models/*.pth と pp_doclayoutv2_weights
ALD_WEB=1 nohup python src/desktop_app.py &
# Macから http://starryai:8550 を開く。ufw のファイアウォール確認。
```
**注意**: セッションごとにモデルをロードするので、同時利用は1〜2名が限界です。これはDewi-sanのゼロインストール利用パスにもなります。

### 4.3 Starrydata2 へのアップロード — **ブロック中**

これはMato-sanの領域なので、判明していることを詳しく書きます。

- **ステージング環境が書き換わっています。** `starrydata-stg.nims.go.jp/starrydata2` は書き直されたVue SPAで動いており、共有された `starrydata_internal_api.yaml` は**古くなっています**。APIは `/paper/` から **`/paperlist/`** に移動し、ルートは**末尾スラッシュなし**（スラッシュ付きは404。catch-allがSPAのHTMLシェルを返すので「200 + HTML」= URLが間違っている、というサインです）。
- **ライブ検証済みのエンドポイント**: `GET /paperlist/getpaperlist/all?projectname&pagelimit&page`（+`d_exist`, `sort_field=sid`, `sort_order`）、`POST /paperlist/postdata/{pk}/{project}`（フォームフィールドは旧APIと同一。アプリの `SaveXYDataUseCase.js` で確認）。JS内で他に `data_api/{pk}/{proj}`, `report_api`, `convert_unit_api/{figureId}`, `getdataset/`, `getversion/{pk}`, `extract_metadata_api` を確認。
- **論文登録**は `/paperlist/project/{Project}` ページの "AddPaper" ボタン。**そのダイアログ／エンドポイントは未捕捉**（DOI入力欄はクリック後にしか現れないため）。
- **⛔ トークン認証がサーバ側で機能していません。** `~/.sd2_token`（40文字）は無視され、エンドポイントはセッション認証のDjangoビューです（GET→302ログイン、POST→CSRF 403）。**Mato-sanがトークン認証を有効にするまでTokenClientは動きません。** Playwrightによるブラウザログイン（MFA込み）のセッション認証は動作します。ただしブラウザを閉じるとすぐ無効化されるようで、保存したセッションは15分ほどで2回とも期限切れになりました。作業はログイン直後のライブセッション内で完結させてください。
- **ネットワーク（ユーザのMac、デュアルホーム）**: Wi-Fi (en0, デフォルトルート) はNIMSネットワークでは**ない**ため403になります。NIMS LANはUSBイーサネット en9 (144.213.x.x)。ステージングサーバはLANからでも直接TLSをRESETするので、**NIMSプロキシ 172.24.4.66:8888 経由が必須**（`proxyout.nims.go.jp` はNIMS DNSでしか解決しません）。しかもそのプロキシ自体がen9にバインドしたソケットからしか到達できません。`tools/starrydata_upload.py` は自動バインド（`nims_bind_ip()`、`$SD2_BIND_IP` で上書き可）とローカルフォワーダ（`start_bound_forwarder`）を実装済みで、Playwrightのブラウザもプロキシを使えるようにしてあります。
- **`tools/starrydata_upload.py` は未コミット**です（2026-07-30/31の修正: `/paperlist/` 接頭辞、末尾スラッシュ削除、NIMSインターフェースバインド + フォワーダ、`sanitize_unit` が µ を分離しない修正 —— サーバのpintが単独の µ を「ミクロン」と解釈して19曲線中6曲線を弾いていました）。`uploadpaper` のパスは**推測のまま**なので検証が必要です。
- **⚠ ステージング上のクリーンアップが必要です。** 2026-07-30のテストで、13本のFeAlSi曲線（5図、figid: `6a6b0700bbbfd31375332025`, `6a6b0706…2034`, `6a6b070b9700e571f9aa6e60`, `6a6b07129700e571f9aa6e6e`, `6a6b07149700e571f9aa6e76`）が **paper pk "599"** に投稿されてしまいました。これはバグで切り詰められたIDをサーバが受理したもので、おそらく sid 599 = 無関係な論文です（FeAlSi論文は登録すらされていません）。**これらの図をステージングUI/APIから削除してください。**その後、AddPaperダイアログ → エンドポイント捕捉 → 正しいpk取得 → 再アップロード、という手順でやり直しが必要です。
- テストコーパス: `scratchpad/fealsi_export.json`（SID-60002 から `build_export_from_kmds` で構築した8図19曲線）。エクスポートビルダーとフォーム構築のロジック自体は検証済みで良好です。

### 4.4 NCMRDスキーマのスリム化 — **設計案のみ**

`AutoLineDigitizer/kmds_cleanup/ANALYSIS.md` に分析があります（sensei の「$defs のエントリが多すぎて雑然としている」という指摘を受けたもの）。

スキーマは 1.43M文字（約357kトークン）ですが、**実際のレコード形状はわずか3%（約45KB）**で、残り97%は埋め込まれた統制語彙です:

| チャンク | サイズ | 内容 |
|---|---|---|
| `$defs/process` | 211 KB, 約912エントリ | 全合成手法とそのパラメータ |
| `$defs/measurement` | 199 KB, 約842エントリ | 全測定技術とパラメータ |
| `properties/system` | 247 KB | システム/材料タクソノミー |
| `properties/materials` | 185 KB | 429物性の語彙 |
| その他の `$defs` | 約48 KB | **本物のレコード形状（小さな17個のdef）** |

構造的ノイズ: description が21%（298KB）、`alias` キー1,519個、`examples` キー2,800個、nullable展開 `[X, "null"]` が4,714個、`{value, uncertainty, unit, measurement}` のリーフラッパーが650回以上重複。

**提案する形**（形状とカタログの分離）:
```
ncmrd_core.json      ~30–50 KB   レコード構造のみ
vocab/properties.json            429物性 + 正準単位
vocab/process.json               合成手法 + パラメータ集合
vocab/measurement.json           測定技術 + パラメータ
vocab/system.json                システムタクソノミー
```
プロセスステップは `{"method": "ball milling", "params": {...}}` となり、`method` は**取り込み時に** `vocab/process.json` に対して検証されます。結果としてスキーマ自体は約30分の1になり、語彙はデータとして存在することになります —— Starrydata3 が既に248物性 + 正準単位を保持しているのと同じ場所です。**これはNCMRD維持側への提案が必要で、まだ着手していません。**

### 4.5 その他の未完了項目

- **`digitization_data` を含むレコードはスキーマ違反を報告し続けます** — 拡張スキーマをどうするかの決定待ち（公式に提案するか、正式に分岐スキーマを持つか）。
- **レビューセッションの画像に有効期限掃除がありません** — 図PNGがDBに溜まり続けます。ディスクは常に97%程度で逼迫しています。
- **PostgreSQL への移行は先送り** — SQLiteは単一キュレータ運用には十分ですが、マルチライタには向きません。
- **ML-ready エクスポート**、**second-pass検証の確信度**（ロードマップのTier 3相当）は未着手。
- **電気化学の語彙拡張16語をNCMRD維持側に提案する** — 公式が採用したら拡張ファイルから項目ごとに削除する設計になっています。
- **ローカルLLM の vision-vs-captions A/B** — `data_sources` について未実施。ユーザの仮説は「MinerUのキャプションで十分」で、captions-onlyで figures 4/4 + 軸の意味的一致約90%と、仮説は有力です。
- **アービトレーション段** — GPU上のMoEモデルは temperature 0 でも実行ごとに揺れます（agendaが3〜5サンプルを見つける）。複数実行を調停する段を入れるのが修正案です。

---

## 5. 次にやるべきこと（優先順位順）

### 5.1 【最優先】184枚の待機中の図をレビューして取り込む

DBには10セッション・184図が `pending` のまま入っています:

| session | PDF | 図数 |
|---|---|---|
| 3 | fealsi.pdf | 9 |
| 4 | polymer.pdf | 21 |
| 5 | nature07853.pdf | 5 |
| 6 | SID- 59253 .pdf | 12 |
| 7 | s42154-019-00080-2.pdf | 19 |
| 8–12 | arxiv 5本 | 35 / 8 / 9 / 10 / 56 |

**推奨手順**（§0の責務分離に従って）: ALDデスクトップアプリで各PDFを開き（バッチ状態が自動ロードされます）、図を確認・修正して *Axes OK* / *Extraction OK* を付け、"Upload approved → Starrydata3" を押す。これがデータベースを実データで満たす最短経路です。

これが済めば、コーパス分位帯・整合性チェック・ファセット検索といった「データ量に依存する機能」が初めて意味を持ち始めます。現在の3レコードでは実質的に評価できません。

### 5.2 大規模コーパスの投入 — pilot100 / pilot10

`paper/pilot100/` に **Wiley (ANIE, APP, ASIA, ADFM) と APS (PRAPPLIED) の全文XML 101本**（14MB）、`paper/pilot10/` に **arXiv PDF 3本**が置いてあります。**どちらも未処理です。**

⚠ **重要な注意**: pilot100 のXMLは**テキストのみで図の画像を含みません**。パイプラインはPDFベースなので、この101本は「NCMRDメタデータ抽出のテスト」には使えますが、**図のデジタイズには使えません**。図も必要なら、同じDOIのPDFをTDM経由で別途取得する必要があります。ここは意思決定が必要な分岐点です:

- (a) XMLコーパスをメタデータ抽出のスケール評価に使い、デジタイズは別のPDFコーパスで行う
- (b) 同DOIのPDFを取得して両方を回す
- (c) XML全文から図キャプション・表を抽出する経路を新設する（NCMRDの表・パッセージ抽出は既に動くので、投資対効果は高いかもしれません）

### 5.3 Starrydata2 アップロードの完成（Mato-san依存）

§4.3の順番で:
1. **ステージングのトークン認証を有効にする**（サーバ側。これが最大のブロッカー）
2. AddPaper ダイアログのエンドポイントを捕捉して文書化する
3. `starrydata_upload.py` の `uploadpaper` パス推測を検証する
4. **pk 599 に誤投稿された13曲線を削除する**
5. `tools/starrydata_upload.py` の未コミット修正をコミットする
6. `fealsi_export.json`（8図19曲線）でエンドツーエンドの実アップロードを実施する

これが通れば「ローカルで検証 → コミュニティDBへ」というループが閉じ、論文 §4.8 の主張が実データで裏付けられます。

### 5.4 スキーマ拡張の決着

`digitization_data` は現在ランタイム変換で注入していますが、これは恒久的な解ではありません。3択です:
- (a) NCMRD維持側に `digitization_data` と電気化学語彙を正式提案する（推奨・論文の主張とも整合）
- (b) 明示的に分岐した拡張スキーマをバージョン管理下に置く
- (c) 現状のランタイム緩和を「意図的な仕様」として文書化して維持する

同時に §4.4 のスキーマスリム化を提案すると、NCMRD側への貢献としてまとまりが良くなります。

### 5.5 運用の堅牢化（小さいが効く）

- レビューセッション画像の有効期限掃除（ディスクが常に逼迫しています）
- `desktop_app.py`（5185行）のモジュール分割
- ローカルLLMのアービトレーション段（実行ごとの揺れ対策）
- Postgres バックエンド（複数キュレータ運用に移行するとき）

---

## 6. 運用上の落とし穴（実際に踏んだもの）

| 症状 | 原因と対処 |
|---|---|
| DBを作り直したのにサーバが古いデータを返す | uvicorn が削除済みinodeを掴んでいます。**必ず再起動。** |
| ディスクが一杯になる | マシンは常時97%程度。スクラッチパッドと `/var/folders/…/sd3_review_*` を掃除してください。図PNGが効きます。 |
| ローカルLLMの抽出が無言で壊れる | Ollamaは `num_ctx/2` あたりでプロンプトを**無言で切り詰めます**（HTTP 200のまま、先頭と末尾だけ残す）。パイプラインはトークン数を推定し、スキーマを圧縮し、`prompt_eval < 0.6×推定` でハードフェイルするようにしてあります。 |
| glm-4.7-flash がJSONスキーマを守らない | glm系は Ollama の `format` グラマーを**無視**します（qwen系は強制します）。`think:false` はネイティブ `/api/chat` でのみ有効（`/v1` では `reasoning_effort:"none"`）。 |
| 抽出が17分ハングする | ollamaのモデル再ロードのチャーン。モデルが暖まってからリトライしてください。 |
| SSHトンネルが切れて気づかない | 自己回復ループで回すこと（§1.2）。 |
| SD2が「200 + HTML」を返す | URLが間違っています（catch-allがSPAシェルを返している）。末尾スラッシュを外し、`/paperlist/` 接頭辞を確認。 |
| SD2が曲線を弾く | 単位の µ が分離していると、サーバのpintが「ミクロン」と解釈します。`'µ W'` ではなく `'µW'` に。 |
| PDFのgrep検証が一致しない | fitzのテキスト抽出はハイフンを U+2010 にします。検証時はこの文字を除去してください。 |
| ドナーマッチングが軸を取り違える | ティックレンジによるドナーマッチングは脆弱です（FeAlSi事件、§7）。新規アップロードは実軸+プロヴェナンスを持つので、このクラスのエラーは今後は起きません。 |

---

## 7. 保存された事故記録（テストで固定済み）

パイプラインの設計を形作った2つの本番事故です。設計判断の理由なので、リファクタする際は必ず読んでください。

1. **メタデータなしアップロードがリッチなレコードを破壊した** — JSONバックアップから復旧。構造的修正が §4.3 のスタブマージガードです。回帰テストで固定してあります。

2. **FeAlSi (SID-60002) の軸取り違え** (2026-07-30に修正済み) — 整合性チェッカーの fail フラグ（PF=S²σ の比が 0.3→30 にドリフト）が、デジタイズされたスタブグラフ（軸が `y='Y'`）が**間違ったNCMRD図の軸に**ドナーマッチされていたことを暴きました。σ と PF のティックレンジが重なるため `_match_donor` が順序依存でパネルを誤割り当てし、さらに2組の系列でA/B凡例ラベルが入れ替わっていました。**曲線の値自体はすべて正しかった**（各曲線は本来のパネル軸で較正されていた）ので、修正は再デジタイズではなくラベル付け直しでした。教訓: **ティックレンジによるドナーマッチングは脆弱**。だから新規アップロードには実軸とプロヴェナンスを載せています。

そして §4.4 の3つ目: **ある論文がGPa正準のフィールドにMPaスケールの値を報告しています**。これは上流の抽出エラーですが、サービスは「黙って直す」のではなく**生の単位とともに忠実に表示**します。統一化がデータを捏造してはならない、という原則です。

---

## 8. 論文の状況

`paper/stam-methods-draft.md`（8ページ組版済みPDF: `paper/stam-methods-draft.pdf`）。
*Science and Technology of Advanced Materials: Methods* 向け。著者: Aditya Acharya, Tomoya Mato, Yukari Katsura。

Katsura先生のレビューを2ラウンド反映済み（実装詳細を設計レベルに抽象化、Section 5「評価と実証」を追加、コードレベルの識別子を削除）。**SD2 vs SD3 の位置づけ**は意図的に「4.3–4.5の単位・語彙機構はNCMRD保存モデルの要件であって、Starrydata2 に対する改善の主張ではない（本稿はその内部実装を検討していない）」としてあります —— こうすればMato-sanからのSD2コード共有が不要になるためです。比較主張をするならコード共有が必要になります。

**PDF再生成**（このマシンにpandoc/LaTeX/popplerがないため）:
```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
  --no-pdf-header-footer --print-to-pdf=OUT.pdf file:///…/paper/stam-methods-print.html
```
`.md` と `print.html` の**両方**を編集する必要があります（別々のファイルです）。

**残っている論文タスク**: §5.1〜5.2 で規模を出せば、Limitations に書いた「実証コーパスは3論文」という弱点を直接埋められます。これが投稿前にできる最も価値の高い改善です。

---

## 9. リリースと配布

- **リリースページ**: `github.com/adityaaa-IIT-BHU/AutoLineDigitizer/releases/latest`
- CI (`release.yml`, 約15分) が macOS + Windows のzipをビルドし、`iter_3000.pth` (543M) と `checkpoint.pth` (1.4G) をリリースに添付します。**1ページですべてが揃い、ダウンロード後はオフラインで動作します。**
- 単一zipは不可能です（GitHubのアセット上限2GiB、macOS zip単体で1.56G）。インストール手順は「アプリを解凍 → Import Models で2つの .pth を読み込む」。
- `checksums.txt` をCIに追加済みです（サポート事例: 同僚の1.5GBダウンロードが切り詰められ Archive Utility error 640 になりました。リリースアセット自体は範囲指定EOCDチェックで無傷を確認。**CDN経由のcurlのサフィックス範囲指定は信用せず、明示的なバイト範囲を使ってください**）。
- `.spec` は `ncmrd_vocab_extensions.json` と `tools/starrydata_upload.py`（+requests）を同梱するので、パッケージ版でもNCMRD読み出しと両方のアップロードボタンが動きます。

---
---

# Part II — English

## 0. The whole system in three minutes

A pipeline that turns paper PDFs into schema-validated, queryable materials data **entirely on one local laptop**. Two repositories, strictly separated:

| | Role | Stack |
|---|---|---|
| **AutoLineDigitizer (ALD)** | Desktop app. Figure discovery → axis calibration → curve extraction → NCMRD metadata extraction → **human approval gate** | Python 3.11 + Flet, local ML (LineFormer / ChartDete / EasyOCR / PP-DocLayoutV2) |
| **Starrydata3 (SD3)** | Self-hosted database service. Validates, stores, normalizes vocabulary, canonicalizes units, indexes, serves cross-paper queries + web UI | FastAPI + SQLModel + SQLite, **no ML dependency** |

**Three design principles** — these are specification, not accident. Change them deliberately or not at all:

1. **Locality** — all inference runs on local CPU/GPU. Only the optional VLM assist calls a remote API; disable it and nothing leaves the machine.
2. **Vocabulary honesty** — a property name that matches nothing in the official NCMRD vocabulary is **stored verbatim and flagged ⚠**, never silently renamed.
3. **Non-destructive ingestion** — records upsert by DOI, but a metadata-free "digitization stub" is **merged into** the existing record and is structurally unable to overwrite it.

**Separation of concerns (user decision, 2026-08-03 — important):** figure **editing lives in the ALD desktop app**. The SD3 web portal is for queue management, NCMRD viewing, validation, browsing, and light fixes. Building a full figure editor in the browser was abandoned (see §4.1).

---

## 1. Startup runbook (after a reboot)

### 1.1 Starrydata3 server (required)

```bash
cd /Users/Shared/starrydata3
.venv/bin/uvicorn starrydata3.app:app --host 127.0.0.1 --port 8300
```
→ http://localhost:8300, interactive API docs at `/docs`.

First-time setup on a fresh machine:
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m starrydata3.manage initdb
.venv/bin/python -m starrydata3.manage seedprops          # 248 official NCMRD terms + 16 extensions
.venv/bin/python -m starrydata3.manage createkey me --scope write   # key printed once
```

⚠ **Trap:** if you delete/recreate `starrydata3.db` while uvicorn is running, the server keeps serving the **old deleted inode**. Always restart uvicorn after `initdb`.

### 1.2 Local-LLM tunnel (when NCMRD extraction runs on the GPU box)

The GPU box (`starryai`) runs ollama bound to localhost, so a **self-healing** SSH tunnel is required:
```bash
while true; do ssh -N -L 11434:localhost:11434 box; sleep 3; done
```
A dropped tunnel makes extraction hang silently. Restart with `pkill -f 'ssh.*-L 11434'`.

### 1.3 Assist sidecar (only for the web portal's figure tools)

```bash
# in the alddev conda env
cd /Users/Shared/AutoLineDigitizer && python src/assist_server.py     # :8390
```
SD3 proxies to it at `/api/v1/assist/{op}` (override with `SD3_ASSIST_URL`). This is what keeps SD3 itself ML-free. If the sidecar is down, SD3 returns 503 with the start command.

### 1.4 ALD desktop app

```bash
conda activate alddev
cd /Users/Shared/AutoLineDigitizer && python src/desktop_app.py
# to serve it as a LAN web service:
ALD_WEB=1 ALD_WEB_PORT=8550 python src/desktop_app.py
```

### 1.5 Ports

| Port | Service |
|---|---|
| 8300 | Starrydata3 (uvicorn) |
| 8390 | ALD assist sidecar |
| 8550 | ALD web mode (flet web) |
| 11434 | ollama (SSH tunnel to the GPU box) |

The portal **write key in plaintext** is at `/Users/Shared/starrydata3/.portal.key` (gitignored). The scratchpad is wiped on reboot — never store durable secrets there.

---

## 2. What works today (verified 2026-08-06)

### 2.1 Starrydata3 — the database service

Test suite: **34 tests, all passing** (`.venv/bin/python -m pytest tests/ -q`).
`tests/test_app.py` (core), `tests/test_tier1.py` (quality features), `tests/test_tier2.py` (composition, queue, facets).

Current DB contents (`starrydata3.db`):

| Table | Rows | Content |
|---|---|---|
| `record` | 3 | epoxy (60001) / FeAlSi thermoelectric (60002) / battery (60003) |
| `graphcurve` | 52 | digitized-curve index |
| `pointvalue` | 64 | single measured values from tables + property trees |
| `property` | 275 | official NCMRD vocabulary + local extensions |
| `reviewsession` / `reviewfigure` | 10 / 184 | batch-extracted papers awaiting review |
| `qualityflag` | 4 | consistency-check results |
| `apikey` | 4 | SHA-256 hashes only |

**Working features:**

- **Schema validation** — NCMRD (KMDS) JSON Schema 2020-12. Exactly two deliberate liberalizations, implemented as runtime transformations so the diff is inspectable (`app.py:52` `ncmrd_schema()`): (a) inject the `digitization_data` extension field, (b) relax format patterns on local identifiers. `POST /api/v1/validate` checks without storing.
- **"Store whole, index derived"** — the NCMRD JSON is stored verbatim; at every ingest the curve index and point-value index are deleted and re-derived (`app.py:632` `store_record`). Improvements to normalization apply retroactively by re-ingesting (`manage.py reindex`).
- **Vocabulary normalization** (`app.py:314` `match_property`) — a conservative cascade: exact match after normalization → curated synonym table (every target verified to exist) → special cases (ΔT; a bare "T" only when its unit is a temperature unit) → unique-substring match, restricted to names > 4 chars with exactly one candidate. Anything else: verbatim + flag.
- **Unit canonicalization** (`units.py`) — SI prefixes (including µ/μ/u variants), leading powers of ten ("10⁴ S/m"), detached prefixes ("mu V"), and the 'm' = metre-or-milli ambiguity. Converts **only between identical base signatures** (V K⁻¹ → µV K⁻¹ yes; across quantities never). °C→K is the one additive-offset special case. Both raw and canonical values are stored.
- **Stub-merge guard** (`app.py:768` `_digitization_stub` / `app.py:798` `merge_digitized`) — structurally detects metadata-free digitization uploads and merges them. **This is the structural fix for a real production incident** that once destroyed a rich FeAlSi record. Pinned by a regression test.
- **Consistency checks** (`checks.py`) — PF = S²σ, ZT = S²σT/κ, σ falling back to 1/ρ. Own SI unit parser handling Ω⁻¹↔S, cm, mu prefixes. Detects ×10ⁿ unit errors and ratio drift (⇒ a mis-calibrated axis). Runs at ingest; re-run with `manage.py recheck`.
- **Corpus percentile bands** (`app.py:1601` `property_band`) — p10/p50/p90 per x-bin across all curves of a property pair, drawn behind the record-page plots. Outliers become visible at a glance.
- **Provenance capsules** (`app.py:1523`) — figure-crop PNG + calibration + extractor + curator, attached to the record. ALD ships this automatically after a successful SD3 upload.
- **Composition parser** (`composition.py`) — chemical formula → normalized `comp_key` + element list (Fe3Al2Si3 → Al0.25Fe0.375Si0.375). Powers `/materials` and `/material/{key}` dossiers.
- **Web UI** — `/` (searchable paper list), `/view/{sid}` (record page), `/ncmrd/{sid}` (full NCMRD editorial viewer), `/explore` (cross-paper property-pair plots), `/materials`, `/find` (range-at-condition search), `/queue`, `/review` (review portal).
- **Exports** — `/ui/export.json` (complete backup; re-upload restores the service), `/ui/export.csv` (long form, every curve point + measured value).

### 2.2 AutoLineDigitizer — acquisition and curation

- **Figure discovery** — four strategies: PP-DocLayoutV2 via MinerU (default; has a *chart* class so multi-panel figures split into individual plots), DocLayout-YOLO, a VLM screener, and direct PyMuPDF raster extraction.
- **Axis calibration** — ChartDete detects 18 chart-element classes → EasyOCR (numeric allowlist + four-way preprocessing ensemble) → **RANSAC** robust regression. Both linear and log fits are attempted; log is selected only when it fits better **and** spans ≥ 1.5 decades.
- **Curve extraction** — LineFormer (Mask2Former + Swin-T, 100 queries, 512×512, threshold 0.3). Each mask reduced to a series by per-column median row. Battery-finetuned variants selectable per figure.
- **Scatter/marker detection** (`marker_extractor.py`, fully rebuilt in v0.8.0) — per-colour-layer architecture (Voronoi LAB layers, halo absorption), filled/hollow discrimination, Scatteract-style template amplification, series split by **colour AND shape**. Regression suite `tools/test_marker_extractor.py` passes on 5 chart classes.
- **NCMRD metadata extraction** (`ncmrd_parallel.py`, 1905 lines) — two-phase, section-parallel: phase 1 establishes publication/sample/material entities with stable IDs; phase 2 extracts figures, tables, and text passages that cross-reference those IDs. Cached beside the PDF, reloaded on reopen.
- **Human review gate** — two independent checkboxes per figure, *Axes OK* and *Extraction OK*; only figures with both checked are eligible for upload. The curator can edit axis names/units, rename curves via legend labels, delete curves, exclude figures, and erase/add points under a magnifier loupe.
- **Optional VLM assist (non-clobbering policy)** — five narrow assists: axis reading, calibration verification, legend labeling, vocabulary canonicalization, extraction audit. **An automatic suggestion is written into a field only if that field is empty or still holds the previous automatic value — a curator's hand edit is never overwritten.** Without a key every assist degrades to a deterministic local fallback.
- **Batch mode** (`src/batch_run.py`) — unattended, whole folder:
  ```bash
  python src/batch_run.py papers/ [--push http://localhost:8300 --push-key sd3_…]
                                  [--model general_v2] [--detector mineru] [--limit N] [--no-ncmrd] [--force]
  ```
  Writes `{stem}_batch/digitizations.json` per PDF (pixel-space series + axis config). **Failures are per-figure and per-paper, never per-batch;** re-running skips PDFs whose state exists. The desktop app auto-loads this state when the PDF is opened, so a thumbnail click restores instead of re-digitizing.
- **Local NCMRD extraction on the GPU box** — `model="local:<ollama-model>"` (`KMDS_MODEL` env). Two-paper evaluation done (§2.4).

### 2.3 Review portal (SD3 side)

The batch runner posts an NCMRD record + figure PNGs + digitization state to `POST /api/v1/review/sessions`; `/review` lists them and `/review/{sid}` curates. Per-figure approve/reject/edit, editable NCMRD JSON with live validation, and **Approve** which converts pixel→data via `_px_to_data` (log-aware), grafts the curves into the record, ingests it into the real database, attaches provenance, and runs consistency checks. The round trip is tested.

**10 sessions / 184 figures are sitting in `pending`** right now (see §5.1).

### 2.4 Measured numbers (in the paper, reproducible)

**Extraction accuracy** — 100 line-chart figures previously hand-digitized by Starrydata curators with WPD; 31 thermoelectric publications, 700 curves, ~14,700 hand-placed points:

| Metric | All 100 | 82 reliable |
|---|---|---|
| Task 6a curve recall (mean / median) | 0.85 / 0.93 | 0.88 / 0.94 |
| Task 6b data-extraction score | 0.63 / 0.70 | 0.71 / 0.75 |
| Curve-identity accuracy | 88% | 92% |
| Per-point error, % of y-range (median) | 2.7 | 2.7 |
| Axis-range error x / y (median) | 1.3% / 2.0% | 1.3% / 1.7% |

**The key insight:** re-scoring the same predictions in pixel space gives a median per-point error of **2.4%** — so **most residual error is axis calibration, not curve tracing**. A calibration was produced for 99/100 figures, but on 8 it was grossly wrong (misread tick label or spurious log detection). That is precisely why *Axes OK* is the first thing the human sees.

**Throughput** (Apple-silicon CPU, no GPU): model load ~4 s once; per figure median 10 s (axis analysis ~9 s dominates, LineFormer < 1 s), mean 19 s. Figure discovery ~10 s for a short article; metadata extraction ~45 s for a four-page letter. **The automated part of a short paper finishes in 2–3 minutes.**

**Local LLM vs Claude** (polymer paper, unseen domain): local 0 violations / 83% grounded / 5.3 min vs Claude 2 violations / 42% grounded, comparable time. The local model is at parity or better for NCMRD extraction.

---

## 3. Architecture map — where everything lives

### Starrydata3 (`/Users/Shared/starrydata3`)

| File | Lines | Content |
|---|---|---|
| `starrydata3/app.py` | 1770 | API, data model, ingestion logic |
| `starrydata3/web.py` | 1199 | every web page (HTML inside Python strings) |
| `starrydata3/checks.py` | 245 | cross-property consistency checks |
| `starrydata3/units.py` | 105 | unit canonicalization |
| `starrydata3/composition.py` | 101 | chemical-formula parser |
| `starrydata3/manage.py` | 266 | CLI: `initdb` `seedprops` `createkey` `listkeys` `reindex` `recheck` `addpapers` `release` |
| `starrydata3/ncmrd_viewer.html` | — | full NCMRD editorial viewer (ported from ALD, auto-load injected) |
| `starrydata3/digitizer/` | — | built StarryDigitizer v1.13.3 dist, served at `/digitizer` |
| `ncmrd_schema.json` | 1.43 MB | NCMRD v15.2.4 schema |
| `ncmrd_vocab_extensions.json` | — | 16 local extension terms (**must stay in sync with the copy in ALD/src**) |

**Key entry points** — start here when changing behavior:

- `app.py:85` `validate_ncmrd()` — schema validation
- `app.py:314` `match_property()` — the vocabulary matching cascade
- `app.py:408` `extract_curves()` — curve index rebuild + donor matching
- `app.py:528` `extract_point_values()` — point-value index from tables + property trees
- `app.py:632` `store_record()` — the ingestion core
- `app.py:768` `_digitization_stub()` / `app.py:798` `merge_digitized()` — the stub-merge guard
- `app.py:1230–1491` — review portal API (push / patch / approve)
- `app.py:1281` `assist()` — proxy to the ALD sidecar
- `app.py:1494` `_px_to_data()` — pixel→data conversion, log-aware

### AutoLineDigitizer (`/Users/Shared/AutoLineDigitizer/src`)

| File | Lines | Content |
|---|---|---|
| `desktop_app.py` | 5185 | the Flet desktop app (**huge**; splitting it is future work) |
| `ncmrd_parallel.py` | 1905 | two-phase NCMRD extraction |
| `mineru_layout/pp_doclayoutv2.py` | 1528 | layout detection |
| `marker_extractor.py` | 814 | scatter/marker detection |
| `chartdete_infer.py` | 711 | chart-element detection |
| `smart_axis_extractor.py` | 643 | axis calibration (RANSAC) |
| `pdf_figures.py` | 602 | figure discovery |
| `vlm_verifier.py` | 540 | VLM assists |
| `batch_run.py` | 360 | batch runner |
| `ncmrd_vocab.py` | 203 | vocabulary matching (the mirror of SD3's) |
| `llm_backend.py` | 203 | backend selection (Claude / local ollama) |
| `assist_server.py` | 176 | compute sidecar (:8390) |
| `starrydata3_client.py` | 73 | upload to SD3 + provenance push |
| `tools/starrydata_upload.py` | — | upload to **Starrydata2** (uncommitted — see §4.3) |

### Data-model invariants (do not break these)

1. **The NCMRD record JSON is the truth; everything else is a rebuildable index.** To add a query feature, derive a new index — do not transform the record.
2. **Unmatched property names stay verbatim and flagged.** Never map to "something close".
3. **Unit conversion only between identical base signatures.** Never coerce across quantities.
4. **A metadata-free upload can never overwrite a stored record.**
5. **Series still in pixel coordinates are never indexed as data** — no fake numbers.

---

## 4. What is half-done or parked (honestly)

### 4.1 StarryDigitizer web embed — **PARKED**

`starry-digitizer` v1.13.3 (t29mato, MIT) is cloned, built with vite, and its dist is committed and served at `/digitizer`, embedded in the review page. The image auto-load bridge works in Chrome.

**Unfinished:** injecting curves and axis config into SD's own stores. A `reactive()` wrap and an `App.vue` native-prop patch were attempted (the latter failed with "no data()"). The postMessage path to write SD's datasets back into session state is also unimplemented.

**User decision 2026-08-03:** "do all the work on the AutoLineDigitizer app and then push to starrydata3 — it's difficult to develop this [web editor]." **The desktop flow is complete and this is the supported path.** Do not resume the SD embed unless asked. If it is resumed — and Mato-san knows SD's internals far better than we do — the injection hook points are `src/application/services/canvasHandler` and `datasetRepository` (they look like singleton stores rather than Pinia).

### 4.2 GPU-box web service — **Mac side done, box install not started**

`ALD_WEB=1 python src/desktop_app.py` serves flet on 0.0.0.0; verified HTTP 200 locally (a Playwright body-text check came back empty, which is expected — flet renders client-side).

**The box install never happened** (the ethernet adapter died for the fifth time). Runbook:
```bash
ssh box
conda create -n ald python=3.11 && conda activate ald
export https_proxy=http://proxyout.nims.go.jp:8888
pip install torch            # CUDA build
pip install mmcv-full==1.7.2 # long compile
pip install -r requirements-dev.txt
git clone --recursive -b feat/pdf-figure-gallery <repo>   # via proxy
# scp weights from the Mac (fast over LAN):
#   ~/AutoLineDigitizer/models/*.pth and pp_doclayoutv2_weights
ALD_WEB=1 nohup python src/desktop_app.py &
# open http://starryai:8550 from the Mac; check ufw.
```
**Caveat:** models load per session, so this is fine for 1–2 concurrent users. It also becomes Dewi-san's zero-install access path.

### 4.3 Starrydata2 upload — **BLOCKED**

This is Mato-san's territory, so here is everything that was established live from inside NIMS (2026-07-30/31):

- **Staging was rewritten.** `starrydata-stg.nims.go.jp/starrydata2` now runs a rewritten Vue SPA, and the shared `starrydata_internal_api.yaml` is **outdated**. The API moved from `/paper/` to **`/paperlist/`**, and routes take **no trailing slash** (with a slash → 404; a catch-all serves the SPA HTML shell for unknown paths, so "200 + HTML" means the URL is wrong).
- **Verified live:** `GET /paperlist/getpaperlist/all?projectname&pagelimit&page` (+`d_exist`, `sort_field=sid`, `sort_order`); `POST /paperlist/postdata/{pk}/{project}` (same form fields as the old API — confirmed in the app's `SaveXYDataUseCase.js`). Also seen in the JS: `data_api/{pk}/{proj}`, `report_api`, `convert_unit_api/{figureId}`, `getdataset/`, `getversion/{pk}`, `extract_metadata_api`. Papers are keyed by a 24-hex ObjectID `pk` **and** an integer `sid`; `postdata` accepts either.
- **Paper registration** is the "AddPaper" button on `/paperlist/project/{Project}`. **The dialog/endpoint was never captured** (the DOI input only appears after clicking).
- **⛔ Token auth is dead server-side.** `~/.sd2_token` (40 chars) is ignored; the endpoints are session-auth Django views (GET → 302 login, POST → CSRF 403). **`TokenClient` cannot work until token auth is enabled on the server.** Session auth via a Playwright browser login (including MFA) does work — but sessions are invalidated shortly after the browser closes (a saved session went stale within ~15 min, twice). Do all work inside the live session right after login.
- **Network (the Mac is dual-homed):** Wi-Fi (en0, the default route) is **not** the NIMS network → the server 403s. NIMS LAN is USB ethernet en9, 144.213.x.x. The staging server **RESETs direct TLS even from the LAN**, so traffic must go through the NIMS proxy `172.24.4.66:8888` (`proxyout.nims.go.jp` only resolves on NIMS DNS), which is itself only reachable with the socket bound to en9. `tools/starrydata_upload.py` already auto-binds (`nims_bind_ip()`, `$SD2_BIND_IP` override) and runs a local forwarder (`start_bound_forwarder`) so the Playwright browser can use the proxy too.
- **`tools/starrydata_upload.py` is uncommitted.** Fixes applied 2026-07-30/31: the `/paperlist/` prefix, no trailing slashes, NIMS interface binding + forwarder, and `sanitize_unit` keeping µ attached (`'µ W'` → `'µW'` — the server's pint parsed a lone µ as *micron*, which rejected 6 of 19 curves). The `uploadpaper` path under `/paperlist/` is **a guess** — verify it.
- **⚠ Cleanup needed on staging.** The 2026-07-30 test posted **13 FeAlSi curves** (5 figures, figids `6a6b0700bbbfd31375332025`, `6a6b0706…2034`, `6a6b070b9700e571f9aa6e60`, `6a6b07129700e571f9aa6e6e`, `6a6b07149700e571f9aa6e76`) to **paper pk "599"** — a bug-truncated id the server accepted; sid 599 is probably some unrelated paper, definitely not FeAlSi (which was never registered). **Please delete those figures via the staging UI/API,** then redo properly: AddPaper dialog → capture endpoint → real pk → upload.
- Test corpus: `scratchpad/fealsi_export.json` (8 figures, 19 curves from SID-60002, built with `build_export_from_kmds`). The export builder and form building are verified good.

### 4.4 NCMRD schema slimming — **design only**

Analysis in `AutoLineDigitizer/kmds_cleanup/ANALYSIS.md`, prompted by sensei's remark that "the `$defs` have too many entries, it's messy."

The schema is 1.43 M chars (~357k tokens), but **only ~3% (~45 KB) is the actual record shape**; the other 97% is embedded controlled vocabularies:

| Chunk | Size | What it is |
|---|---|---|
| `$defs/process` | 211 KB, ~912 entries | every synthesis method + parameters |
| `$defs/measurement` | 199 KB, ~842 entries | every measurement technique + parameters |
| `properties/system` | 247 KB | system/material taxonomy |
| `properties/materials` | 185 KB | the 429-property vocabulary |
| everything else in `$defs` | ~48 KB | **the real record shape (17 small defs)** |

Structural noise on top: descriptions are 21% of the file (298 KB of prose), 1,519 `alias` keys, 2,800 `examples` keys, 4,714 `[X, "null"]` nullable expansions, and the `{value, uncertainty, unit, measurement}` leaf wrapper repeated 650+ times instead of being `$ref`d once.

**Proposed shape** — separate the *shape* from the *catalogs*:
```
ncmrd_core.json      ~30–50 KB   record structure only
vocab/properties.json            429 properties + canonical units
vocab/process.json               synthesis methods + parameter sets
vocab/measurement.json           measurement techniques + parameters
vocab/system.json                system taxonomy
```
A process step becomes `{"method": "ball milling", "params": {...}}` with `method` validated against `vocab/process.json` **at ingest**. The schema shrinks ~30×, and the vocabularies live as data — exactly where Starrydata3 already keeps the 248-property vocabulary and canonical units. **This needs a proposal to the NCMRD maintainers; it has not been started.**

### 4.5 Other open items

- **Records containing `digitization_data` still report schema violations**, pending a decision on the extended schema.
- **Review-session images have no expiry sweep** — figure PNGs accumulate in the DB, and this machine's disk sits around 97% full.
- **PostgreSQL deferred** — SQLite is fine for a single curator, wrong for multi-writer.
- **ML-ready export** and **second-pass verification confidence** (Tier 3 of the roadmap) not started.
- **Propose the 16 electrochemical extension terms upstream** — the extension file is designed to be deleted term-by-term as official NCMRD adopts them.
- **Local-LLM vision-vs-captions A/B for `data_sources`** never run. The hypothesis is that MinerU captions suffice; captions-only already got figures 4/4 and ~90% semantic axis agreement, so it looks strong.
- **Arbitration stage** — the MoE model on the GPU box varies between runs even at temperature 0 (the agenda pass found 3–5 samples across runs). Arbitrating across runs is the fix.

---

## 5. What to build next, in priority order

### 5.1 Highest priority: review and ingest the 184 waiting figures

The database holds 10 sessions / 184 figures still `pending`:

| session | PDF | figures |
|---|---|---|
| 3 | fealsi.pdf | 9 |
| 4 | polymer.pdf | 21 |
| 5 | nature07853.pdf | 5 |
| 6 | SID- 59253 .pdf | 12 |
| 7 | s42154-019-00080-2.pdf | 19 |
| 8–12 | five arXiv papers | 35 / 8 / 9 / 10 / 56 |

**Recommended path**, following the separation of concerns in §0: open each PDF in the ALD desktop app (batch state auto-loads), check and correct the figures, tick *Axes OK* / *Extraction OK*, press "Upload approved → Starrydata3". This is the shortest route to a database with real content.

Until this is done, the data-volume-dependent features — corpus percentile bands, consistency checks, faceted search — cannot really be evaluated. Three records is not a corpus.

### 5.2 Scale up the corpus — pilot100 / pilot10

`paper/pilot100/` holds **101 full-text XMLs** from Wiley (ANIE, APP, ASIA, ADFM) and APS (PRAPPLIED), 14 MB. `paper/pilot10/` holds **3 arXiv PDFs**. **Neither has been processed.**

⚠ **Important:** the pilot100 XMLs are **text only — they contain no figure images**. The pipeline is PDF-based, so these 101 articles are usable for testing NCMRD metadata extraction at scale but **not for figure digitization**. If figures are needed, the matching PDFs must be fetched separately via TDM. This is a decision point:

- (a) use the XML corpus to evaluate metadata extraction at scale; digitize from a separate PDF corpus
- (b) fetch the PDFs for the same DOIs and run both halves
- (c) build a path that extracts figure captions and tables from XML full text — NCMRD table/passage extraction already works, so this may be high value for low effort

### 5.3 Finish the Starrydata2 upload (depends on Mato-san)

In the order given in §4.3:
1. **Enable token auth on staging** (server-side — this is the blocker)
2. Capture and document the AddPaper dialog endpoint
3. Verify the guessed `uploadpaper` path in `starrydata_upload.py`
4. **Delete the 13 curves mis-posted to pk 599**
5. Commit the uncommitted fixes in `tools/starrydata_upload.py`
6. Run a real end-to-end upload with `fealsi_export.json` (8 figures, 19 curves)

Once this works, the "verify locally → publish to the community database" loop closes and the claim in paper §4.8 is backed by real data.

### 5.4 Settle the schema extension

`digitization_data` is currently injected by runtime transformation, which is not a permanent answer. Three options:
- (a) formally propose `digitization_data` and the electrochemical vocabulary to the NCMRD maintainers (recommended; consistent with the paper's argument)
- (b) maintain an explicitly forked extended schema under version control
- (c) document the current runtime liberalization as deliberate and keep it

Proposing §4.4's schema slimming at the same time would make a coherent contribution back to NCMRD.

### 5.5 Operational hardening (small, high value)

- Expiry sweep for review-session images (the disk is chronically full)
- Split `desktop_app.py` (5185 lines) into modules
- Arbitration stage for the local LLM (run-to-run variance)
- Postgres backend when the deployment grows past one curator

---

## 6. Operational traps (all of these actually bit us)

| Symptom | Cause and fix |
|---|---|
| Server serves stale data after recreating the DB | uvicorn holds the deleted inode. **Always restart it.** |
| Disk fills up | The machine sits at ~97%. Clean the scratchpad and `/var/folders/…/sd3_review_*`. Figure PNGs are the bulk. |
| Local-LLM extraction silently breaks | Ollama **silently truncates prompts** at ~`num_ctx/2`, keeping head + tail, still returning HTTP 200. The pipeline estimates tokens, compacts schemas, buckets `num_ctx`, and hard-fails when `prompt_eval < 0.6 × estimate`. |
| glm-4.7-flash ignores the JSON schema | glm **ignores** Ollama's `format` grammar (qwen enforces it). `think:false` works only on the native `/api/chat` (on `/v1` use `reasoning_effort:"none"`). |
| A call hangs for ~17 minutes | Ollama model-reload churn. Retry when the model is warm. |
| The SSH tunnel dies unnoticed | Run it in the self-healing loop (§1.2). |
| SD2 returns "200 + HTML" | Wrong URL — the catch-all served the SPA shell. Drop the trailing slash; check the `/paperlist/` prefix. |
| SD2 rejects curves | A detached µ in the unit; the server's pint reads it as *micron*. Send `'µW'`, not `'µ W'`. |
| Grep-verifying the PDF fails | fitz text extraction hyphenates with U+2010; strip that character before matching. |
| Donor matching attaches the wrong axes | Tick-range donor matching is fragile (the FeAlSi incident, §7). New uploads carry real axes + provenance, so this class of error is dead going forward. |

---

## 7. Preserved incidents (pinned by tests)

Two production incidents shaped the design. Read them before refactoring — they are the *why*.

1. **A metadata-free upload replaced a rich stored record.** Recovered from the JSON backup. The structural fix is the stub-merge guard (§2.1), pinned by a regression test: a placeholder-axis stub merged into a rich record must leave the title intact and attach its curve to the real temperature–ZT graph.

2. **FeAlSi (SID-60002) axis mix-up** (fixed 2026-07-30). The consistency checker's fail flags — the PF = S²σ ratio drifting from 0.3 to 30 — exposed that digitized stub graphs (axes `y='Y'`) had been donor-matched to the **wrong** NCMRD figure axes: σ and PF tick ranges overlap, so `_match_donor` assigned panels order-dependently. Two series pairs also had their A/B legend labels swapped. **The curve values were all correct** (each was calibrated on its true panel axis), so the fix was relabeling, not re-digitizing. Afterwards all four samples pass PF = S²σ (n-type deviation 1.0–1.2%, p-type 5.5–9.2% on a sparse 49-point series). Lesson: **tick-range donor matching is fragile** — which is why new uploads carry real axes and provenance.

And a third, kept deliberately visible: one source paper reports MPa-scale values in a field whose canonical unit is GPa. That is an upstream extraction error, and the service **displays it faithfully with its raw unit** rather than "correcting" it — unification must never manufacture data.

---

## 8. Paper status

`paper/stam-methods-draft.md`, typeset as an 8-page PDF at `paper/stam-methods-draft.pdf`.
For *Science and Technology of Advanced Materials: Methods*. Authors: Aditya Acharya, Tomoya Mato, Yukari Katsura.

Two rounds of Katsura-sensei's review are incorporated: implementation details abstracted to design level, Section 5 "Evaluation and demonstration" added, code-level identifiers removed. **The SD2-vs-SD3 framing is deliberate:** the paper states that the unit/vocabulary machinery of §4.3–4.5 is a *requirement of the NCMRD storage model*, **not** a claimed improvement over Starrydata2, "whose internal implementation this paper does not examine." This was chosen so that no SD2 code sharing from Mato-san is needed — comparative claims would require it.

**Regenerating the PDF** (no pandoc/LaTeX/poppler on this machine):
```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
  --no-pdf-header-footer --print-to-pdf=OUT.pdf file:///…/paper/stam-methods-print.html
```
You must edit **both** the `.md` and `print.html` — they are separate files.

**Remaining paper work:** doing §5.1–5.2 directly repairs the stated limitation "the demonstration corpus is three papers." That is the single highest-value improvement available before submission.

---

## 9. Releases and distribution

- **Release page:** `github.com/adityaaa-IIT-BHU/AutoLineDigitizer/releases/latest`
- CI (`release.yml`, ~15 min) builds macOS + Windows zips **and** attaches `iter_3000.pth` (543 M) and `checkpoint.pth` (1.4 G) to the release, so one page is everything and it works offline after the download.
- A single zip is impossible (GitHub's 2 GiB per-asset cap; the macOS zip alone is 1.56 G). Install flow: unzip the app, then **Import Models** with the two `.pth` files.
- `checksums.txt` is now produced by CI. (Support case: a labmate's 1.5 GB download was truncated → Archive Utility error 640. The release asset itself was verified intact with a ranged EOCD check — **beware curl suffix-ranges through the CDN; use explicit byte ranges**.)
- The `.spec` bundles `ncmrd_vocab_extensions.json` and `tools/starrydata_upload.py` (+requests), so the packaged app has the NCMRD readout and both upload buttons.

---

## 10. Contact / handover checklist

**Aditya Acharya** — aditya.acharya.bme23@itbhu.ac.in

Immediate actions for whoever picks this up:

- [ ] Read §1 and get all four services running
- [ ] Run both test suites and confirm they are green (SD3: 34 passing)
- [ ] Review + ingest the 184 pending figures (§5.1)
- [ ] Decide the pilot100 XML-vs-PDF question (§5.2)
- [ ] Enable token auth on SD2 staging and clean up pk 599 (§4.3, §5.3)
- [ ] Decide the schema-extension question (§5.4)
- [ ] Keep `ncmrd_vocab_extensions.json` in sync between the two repos
- [ ] Never break the five data-model invariants in §3

**Do not** resume the StarryDigitizer web embed unless explicitly asked (§4.1). The desktop flow is the supported path and it is complete.

---
---

# 追補 / Addendum — 2026-08-06 夕方 (same day, later)

前半の記述以降に判明した事項です。**§1 のランブックと mmcv の導入手順は、この追補で
上書きされます。** Findings from after the main document was written. **These supersede
the mmcv install instructions given earlier in §1.**

A formatted bilingual version of this addendum is published at:
https://claude.ai/code/artifact/2ec5a8c3-e4f8-4826-a47c-e04c60e4e990

## A.1 ⚠ ボックスの `ald` 環境は現在 torch が存在しません / The box's `ald` env currently has no torch

修復作業の途中でtorchをアンインストール後、再インストールに失敗しました。
`~/miniconda3/envs/ald` は「新規の障害」ではなく**作業途中の状態**です。他への影響は
なく、Ollamaサービスとコーパスは無変更です。ログ: ボックス上の `~/ald_stack_fix.log`。

My last repair attempt uninstalled torch and then failed to reinstall it. The conda env
`~/miniconda3/envs/ald` on the box is **mid-surgery, not freshly broken.** Nothing else
depends on it — the Ollama service and the TDM corpus are untouched.

## A.2 mmcv がビルドできない本当の理由 / The real reason mmcv would not build

**原因1（バージョン系統の誤り）:** 本アプリが使うのは `mmcv-full 1.7.2` + `mmdet 2.28.2`
の旧系統であり、mmcv 2.x ではありません。

**原因2（CUDA不一致）:** mmcv はインストール時にCUDAカーネルをコンパイルします。ボックスの
ツールキットは **CUDA 12.0** ですが、導入されていた torch は **CUDA 13** 向けビルドで、
mmcv はこの不一致ではビルドを拒否します（`The detected CUDA version mismatches the
version that was used to compile PyTorch`）。

**正しい対処（コンパイル不要）:**

```bash
# ビルド済みwheelを使うのでCUDA不一致は発生しない / prebuilt wheels: nothing compiles
pip install torch==2.0.1 torchvision==0.15.2 --index-url https://download.pytorch.org/whl/cu118
pip install mmcv-full==1.7.2 -f https://download.openmmlab.com/mmcv/dist/cu118/torch2.0/index.html
pip install mmdet==2.28.2 mmengine==0.10.7
```

**残る障害:** 上記は**ダウンロード段階で失敗します**。`download-r2.pytorch.org` の証明書を
NIMSプロキシ経路で検証できません（`CERTIFICATE_VERIFY_FAILED`）。TLSチェーンの問題であり
ホスト許可リストの問題ではないため `--trusted-host` では解決しません。**プロキシのCA証明書を
環境に導入するか、wheelをオフラインで持ち込む必要があります。**

The install above **fails at the download step**: `download-r2.pytorch.org` presents a
certificate the NIMS proxy chain cannot verify. `--trusted-host` does not help — the
failure is in the TLS chain, not the host allowlist. Needs the proxy's CA certificate
installed in the environment, or the wheels carried in offline. This is the only thing
standing between us and the browser-served app (`ALD_WEB=1`, port 8550).

## A.3 画像AI補助が HTTP 500 になる / Vision assists return HTTP 500

**症状:** バッチ実行中、軸名の読み取りと凡例ラベル付けが Ollama から `HTTP 500` を返します。
曲線トレースは動作するためバッチ自体は完了しますが、この2機能は**無音で無効**になります。

**原因（`ollama ps` で確定）: モデルがGPUに載っていません。** `qwen2.5vl:32b` は
コンテキスト49152で **54 GB** を要求し、RTX 4090 の24GBに全く収まりません。その結果
Ollamaは **100% CPU** にフォールバックし、実用に耐えない速度になります。さらに悪いことに、
この半端に読み込まれたモデルが `Stopping...` 状態で居座り、**NCMRD用テキストモデルの
読み込みまでブロック**します。10報のバッチが1報目で19分間停止したのはこれが原因でした。

```
NAME             ID              SIZE     PROCESSOR    CONTEXT    UNTIL
qwen2.5vl:32b    3edc3a52fe98    54 GB    100% CPU     49152      Stopping...
```

**即時の回避策（実装済み・コミット `8c00f41`）:** `ALD_NO_VLM_ASSIST=1` で画像補助を
完全に無効化できます。この2機能は「あれば良い」補助であり、バッチ全体を止める価値は
ありません。

**恒久対処（root権限が必要）:** GPUに収まる画像モデルを使うこと。**本日ボックス上に
`qwen2.5vl:7b` が読み込まれているのを確認しました（22 GB・100% GPU・コンテキスト32768）**
— こちらはGPUで動作します。ただし NCMRDモデル `glm-4.7-flash-48k`（約22GB）とは依然
同時常駐できないため、`OLLAMA_MAX_LOADED_MODELS=1` と短い `keep_alive` を併せて設定し、
確実に入れ替わるようにしてください。

**Root cause (confirmed via `ollama ps`): the model does not fit on the GPU at all.**
`qwen2.5vl:32b` wants **54 GB** at context 49152 against the 4090's 24 GB, so Ollama falls
back to **100% CPU** — unusably slow — and the half-loaded model then sits in `Stopping...`
and **blocks the text model the NCMRD stage needs.** This is what stalled the 10-paper
batch on paper 1 for 19 minutes.

**Immediate workaround (implemented, commit `8c00f41`):** `ALD_NO_VLM_ASSIST=1` disables
the vision assists entirely. They are nice-to-have; they are not worth stalling a batch.

**Permanent fix (needs root):** serve a vision model that fits. **`qwen2.5vl:7b` was
observed loaded on the box today at 22 GB / 100% GPU / context 32768** — that one runs on
the GPU. It still cannot coexist with the ~22 GB NCMRD model, so pair it with
`OLLAMA_MAX_LOADED_MODELS=1` and a short `keep_alive` so the two swap deterministically.

## A.4 KMDS → NCMRD の名称変更完了 / Rename completed

両リポジトリの全体をNCMRDに統一しました（モジュール、スキーマ・語彙ファイル、ビューアHTML、
ルート `/ncmrd/{sid}`、UI文字列、環境変数、出力ディレクトリ）。**既存データは引き続き
読み込めます:** 環境変数 `KMDS_MODEL` はフォールバックとして有効、既存の `{stem}_kmds/`
フォルダも認識され、DBの `kmds` カラムは起動時に自動で `ncmrd` へ移行されます。

Both repos now say NCMRD throughout, with backward compatibility kept: `KMDS_MODEL` still
honoured as an env fallback, existing `{stem}_kmds/` output folders still found, and the
database migrates its `kmds` column to `ncmrd` automatically on startup.
Tests: SD3 34 passing, ALD 57 passing.

## A.5 ローカルGPUバッチの実行例 / Verified local-GPU batch command

10報の実行で検証済み（arXivオープンアクセス論文、`paper/pilot10/`）。
Verified on a 10-paper run:

```bash
cd /Users/Shared/AutoLineDigitizer
ALD_LLM_BACKEND=local \
NCMRD_MODEL="local:glm-4.7-flash-48k:latest" \
ALD_LOCAL_LLM_URL="http://127.0.0.1:11434/v1" \
/usr/bin/caffeinate -is ~/miniconda3/envs/alddev/bin/python \
  src/batch_run.py <folder-of-pdfs> \
  --push http://127.0.0.1:8300 --push-key "$(cat /Users/Shared/starrydata3/.portal.key)"
```

**前提:** SSHトンネルが生きていること。トンネルはクライアント再起動で切れます。AI機能が
エラーになったら**最初に確認すべき箇所**です。Prerequisite: the SSH tunnel must be up. It
dies on client reboot — the first thing to check when AI features error.

```bash
setsid nohup bash -c 'while true; do ssh -N -o BatchMode=yes \
  -o ServerAliveInterval=15 -o ExitOnForwardFailure=yes \
  -L 11434:localhost:11434 box; sleep 3; done' >/dev/null 2>&1 &
curl -s http://localhost:11434/api/tags   # 約16モデルが列挙されれば成功
```

## A.6 最優先事項 / Highest priority

1つだけ実施するなら **図版画像ファイルの保存場所の特定** です（本編 §5.2 のパイロットは
すべてこれ待ちです）。技術作業ではなく確認事項です。約5,500万件・25TBの抽出済み画像が
どのストレージにあり、ボックスから読み取れるか（マウントまたはコピー）。別ホストからのみ
到達可能であれば、パイプラインをそちらで実行することも可能です。

If only one thing gets done: **find out where the figure image files are stored.** The
index knows about 7.2 M figures but the image files are not on the box (~25 TB against the
box's 12 TB), so a pilot run today would produce NCMRD text records with **zero digitized
curves**. It is a question, not an engineering task, and everything about scaling to the
corpus waits on the answer.

## A.7 ディスク逼迫 / Disk pressure on the curator Mac

本機は慢性的に約97%使用中です（実測: 空き434MiB）。長時間バッチや図のエクスポートで
枯渇し得ます。長時間実行の前に `df -h /` を確認してください。

The workstation runs at ~97% full chronically (observed: 434 MiB free). Long batch runs
and figure exports can fill it. Check `df -h /` before long runs.
