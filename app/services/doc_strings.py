"""Every fixed, user-visible string the workbook renderers write themselves.

The content of a generated workbook is the model's and already arrives in
the user's language. The chrome around it (tab names, column headers,
legend lines, status words, notes, dates) is written by the renderers in
`doc_templates.py` and `workbook_plan.py`, and until 2026-11-26 it was
English for everybody: a Brazilian Portuguese account got a plan in
Portuguese under tabs named "Slip" and "Receipts" (Shoulder Surf report).

WHY A PYTHON TABLE AND NOT config/remote/report-strings.{locale}.json.
That mechanism was the first candidate and it does not fit:

  * report-strings is a SERVED config. The client fetches it and caches it
    per family, so adding roughly 150 renderer-only keys would ship them
    to every phone that never reads one of them, and would need a version
    bump in all five files plus a pointer sync after deploy before the
    renderer saw a single key.
  * Several of these strings are LOAD-BEARING. A status word is written
    into a cell, into the Status dropdown and into the conditional
    formatting formulas that compare against it, and a sheet name is
    quoted inside every cross-sheet formula. A hot-editable copy with a
    comma in a status word, or a 32 character tab name, produces a file
    Excel refuses to open. Here they are pinned by tests that ship with
    the code that depends on them.
  * A deploy never changes served values on its own (overlay first), so
    for a window after every deploy the renderer would run new code
    against an old table.

Rules for anyone adding a string:
  * Add the key to EVERY locale. English is the fallback for a missing
    key and for a locale we do not carry, and a test fails on a gap.
  * No em dash, no en dash, no dash used as punctuation. A hyphen inside a
    real hyphenated word is fine. `tests/test_doc_strings.py` scans this
    table, because the config dash scans only reach config/remote.
  * Sheet names: 31 characters at most, none of \\ / ? * [ ] : and no
    apostrophe, unique within the workbook.
  * Status words: no comma and no double quote (they are joined into a
    data validation list and quoted inside formulas).
  * pt is Brazilian Portuguese, informal ("você").
"""

from __future__ import annotations

from datetime import date

from app.services.locale_injection import normalize_locale

LOCALES = ("en", "es", "fr", "ja", "pt")
DEFAULT_LOCALE = "en"

# Sheet-name keys, in no particular order. `sheet_names()` returns them.
SHEET_KEYS = ("sheet_gantt", "sheet_progress", "sheet_slip", "sheet_receipts")
STATUS_KEYS = ("complete", "in_progress", "on_hold", "not_started", "blocked")

# key -> locale -> text. Authored side by side so a missing translation is
# visible in review; `STRINGS` below is the same table keyed by locale.
_BY_KEY: dict[str, dict[str, str]] = {
    # ---- sheet names (Excel rules apply, see module docstring) ----------
    "sheet_gantt": {
        "en": "Gantt View", "es": "Vista Gantt", "fr": "Vue Gantt",
        "ja": "ガントビュー", "pt": "Visão Gantt"},
    "sheet_progress": {
        "en": "Progress", "es": "Progreso", "fr": "Avancement",
        "ja": "進捗", "pt": "Progresso"},
    "sheet_slip": {
        "en": "Slip", "es": "Desvíos", "fr": "Glissements",
        "ja": "期日の変動", "pt": "Desvios"},
    "sheet_receipts": {
        "en": "Receipts", "es": "Evidencias", "fr": "Justificatifs",
        "ja": "根拠", "pt": "Evidências"},

    # ---- status words (cell value, dropdown entry, formula literal) -----
    "status_complete": {
        "en": "Complete", "es": "Completada", "fr": "Terminée",
        "ja": "完了", "pt": "Concluída"},
    "status_in_progress": {
        "en": "In Progress", "es": "En curso", "fr": "En cours",
        "ja": "進行中", "pt": "Em andamento"},
    "status_on_hold": {
        "en": "On Hold", "es": "En espera", "fr": "En attente",
        "ja": "保留", "pt": "Em espera"},
    "status_not_started": {
        "en": "Not Started", "es": "Sin iniciar", "fr": "Non commencée",
        "ja": "未着手", "pt": "Não iniciada"},
    "status_blocked": {
        "en": "Blocked", "es": "Bloqueada", "fr": "Bloquée",
        "ja": "ブロック中", "pt": "Bloqueada"},

    # ---- Gantt View: status strip ---------------------------------------
    "strip_as_of": {
        "en": "As of the {date} standup",
        "es": "Según la reunión del {date}",
        "fr": "D'après la réunion du {date}",
        "ja": "{date}の会議時点",
        "pt": "Conforme a reunião de {date}"},
    "strip_generated": {
        "en": "generated {date}", "es": "generado el {date}",
        "fr": "généré le {date}", "ja": "{date}に生成",
        "pt": "gerado em {date}"},
    "strip_tasks": {
        "en": "{n} tasks", "es": "{n} tareas", "fr": "{n} tâches",
        "ja": "タスク{n}件", "pt": "{n} tarefas"},
    "strip_coverage": {
        "en": "dates on {dated} of {total} activities",
        "es": "fechas en {dated} de {total} actividades",
        "fr": "dates sur {dated} des {total} activités",
        "ja": "{total}件中{dated}件に日付あり",
        "pt": "datas em {dated} de {total} atividades"},
    "strip_overall": {
        "en": "{pct}% complete overall",
        "es": "{pct}% completado en total",
        "fr": "{pct} % terminé au total",
        "ja": "全体の{pct}%が完了",
        "pt": "{pct}% concluído no total"},
    "strip_at_risk": {
        "en": "{n} at risk as of {date}",
        "es": "{n} en riesgo al {date}",
        "fr": "{n} à risque au {date}",
        "ja": "{date}時点でリスクあり{n}件",
        "pt": "{n} em risco em {date}"},
    "strip_none_at_risk": {
        "en": "none at risk as of {date}",
        "es": "ninguna en riesgo al {date}",
        "fr": "aucune à risque au {date}",
        "ja": "{date}時点でリスクなし",
        "pt": "nenhuma em risco em {date}"},
    "strip_next_milestone": {
        "en": "next milestone: {name} {date}",
        "es": "próximo hito: {name} {date}",
        "fr": "prochain jalon : {name} {date}",
        "ja": "次のマイルストーン: {name} {date}",
        "pt": "próximo marco: {name} {date}"},
    "week_of": {
        "en": "Week of {date}", "es": "Semana del {date}",
        "fr": "Semaine du {date}", "ja": "{date}の週",
        "pt": "Semana de {date}"},
    "project_default": {
        "en": "Project", "es": "Proyecto", "fr": "Projet",
        "ja": "プロジェクト", "pt": "Projeto"},

    # ---- Gantt View: column headers (a newline is a deliberate wrap) ----
    "col_task_name": {
        "en": "Task Name", "es": "Tarea", "fr": "Tâche",
        "ja": "タスク名", "pt": "Tarefa"},
    "col_at_risk": {
        "en": "At\nRisk", "es": "En\nriesgo", "fr": "À\nrisque",
        "ja": "リスク", "pt": "Em\nrisco"},
    "col_status": {
        "en": "Status", "es": "Estado", "fr": "Statut",
        "ja": "ステータス", "pt": "Status"},
    "col_start": {
        "en": "Start\nDate", "es": "Fecha de\ninicio", "fr": "Date de\ndébut",
        "ja": "開始日", "pt": "Data de\ninício"},
    "col_end": {
        "en": "End\nDate", "es": "Fecha de\nfin", "fr": "Date de\nfin",
        "ja": "終了日", "pt": "Data de\ntérmino"},
    "col_predecessors": {
        "en": "Predecessors", "es": "Predecesoras", "fr": "Prédécesseurs",
        "ja": "先行タスク", "pt": "Predecessoras"},
    "col_assigned_to": {
        "en": "Assigned To", "es": "Asignada a", "fr": "Assignée à",
        "ja": "担当者", "pt": "Responsável"},
    "col_pct_done": {
        "en": "%\nDone", "es": "%\nhecho", "fr": "%\nfait",
        "ja": "完了率", "pt": "%\nfeito"},
    "col_effort": {
        "en": "Effort", "es": "Esfuerzo", "fr": "Effort",
        "ja": "工数", "pt": "Esforço"},
    "col_float": {
        "en": "Float\nwork days", "es": "Holgura\ndías háb.",
        "fr": "Marge\njours ouvrés", "ja": "余裕\n営業日",
        "pt": "Folga\ndias úteis"},

    # ---- Gantt View: status key and legend ------------------------------
    "key_title": {
        "en": "STATUS KEY", "es": "LEYENDA DE ESTADO",
        "fr": "LÉGENDE DES STATUTS", "ja": "ステータス凡例",
        "pt": "LEGENDA DE STATUS"},
    "key_complete": {
        "en": "COMPLETED", "es": "COMPLETADA", "fr": "TERMINÉE",
        "ja": "完了", "pt": "CONCLUÍDA"},
    "key_in_progress": {
        "en": "IN PROGRESS", "es": "EN CURSO", "fr": "EN COURS",
        "ja": "進行中", "pt": "EM ANDAMENTO"},
    "key_on_hold": {
        "en": "ON HOLD", "es": "EN ESPERA", "fr": "EN ATTENTE",
        "ja": "保留", "pt": "EM ESPERA"},
    "key_not_started": {
        "en": "NOT STARTED", "es": "SIN INICIAR", "fr": "NON COMMENCÉE",
        "ja": "未着手", "pt": "NÃO INICIADA"},
    "legend_overlay": {
        "en": ("darker bar section = completed share, drives from % Done "
               "(deep red when the task is at risk)"),
        "es": ("tramo más oscuro de la barra = parte completada, según "
               "% hecho (rojo oscuro si la tarea está en riesgo)"),
        "fr": ("partie plus foncée de la barre = part terminée, d'après "
               "% fait (rouge foncé si la tâche est à risque)"),
        "ja": ("バーの濃い部分 = 完了した割合。完了率の値で決まります"
               "（タスクにリスクがある場合は濃い赤）"),
        "pt": ("trecho mais escuro da barra = parte concluída, conforme "
               "% feito (vermelho escuro se a tarefa está em risco)")},
    "legend_blank_pct": {
        "en": ("blank % Done = nobody stated it in a meeting; values are "
               "never estimated"),
        "es": ("% hecho en blanco = nadie lo dijo en una reunión; los "
               "valores nunca se estiman"),
        "fr": ("% fait vide = personne ne l'a indiqué en réunion ; les "
               "valeurs ne sont jamais estimées"),
        "ja": ("完了率が空欄 = 会議で誰も数値を言っていません。値を推定"
               "することはありません"),
        "pt": ("% feito em branco = ninguém falou esse número em reunião; "
               "os valores nunca são estimados")},
    "legend_phase_pct": {
        "en": ("phase % = duration-weighted; unstarted tasks count as 0, "
               "Complete tasks as 100"),
        "es": ("% de fase = ponderado por duración; las tareas sin iniciar "
               "cuentan como 0 y las completadas como 100"),
        "fr": ("% de phase = pondéré par la durée ; les tâches non "
               "commencées comptent pour 0, les tâches terminées pour 100"),
        "ja": ("フェーズの% = 期間で重み付け。未着手のタスクは0、完了した"
               "タスクは100として数えます"),
        "pt": ("% da fase = ponderado pela duração; tarefas não iniciadas "
               "contam como 0 e as concluídas como 100")},
    "legend_float": {
        "en": ("Float = working days this task can slip before the project "
               "end moves; 0 means it is on the critical path (live, "
               "recalculates when you edit a date)"),
        "es": ("Holgura = días hábiles que esta tarea puede retrasarse "
               "antes de mover el fin del proyecto; 0 significa que está "
               "en la ruta crítica (en vivo, se recalcula cuando editas "
               "una fecha)"),
        "fr": ("Marge = jours ouvrés dont cette tâche peut glisser avant "
               "de déplacer la fin du projet ; 0 signifie qu'elle est sur "
               "le chemin critique (en direct, recalculé quand tu modifies "
               "une date)"),
        "ja": ("余裕 = プロジェクトの終了日を動かさずにこのタスクが遅れ"
               "られる営業日数。0はクリティカルパス上にあることを示します"
               "（日付を編集すると自動で再計算）"),
        "pt": ("Folga = dias úteis que esta tarefa pode atrasar antes de "
               "mexer no fim do projeto; 0 significa que ela está no "
               "caminho crítico (ao vivo, recalcula quando você edita uma "
               "data)")},
    "legend_conditional": {
        "en": ("shaded End Date = the meeting stated a condition on that "
               "date, not a firm commitment; hover the cell for the "
               "condition"),
        "es": ("fecha de fin sombreada = la reunión puso una condición a "
               "esa fecha, no es un compromiso firme; pasa el cursor por "
               "la celda para ver la condición"),
        "fr": ("date de fin grisée = la réunion a posé une condition sur "
               "cette date, ce n'est pas un engagement ferme ; survole la "
               "cellule pour voir la condition"),
        "ja": ("終了日の網掛け = 会議でその日付に条件が付いており、確定した"
               "約束ではありません。セルにカーソルを合わせると条件が表示"
               "されます"),
        "pt": ("data de término sombreada = a reunião colocou uma condição "
               "nessa data, não é um compromisso firme; passe o cursor na "
               "célula para ver a condição")},
    "comment_conditional": {
        "en": "Conditional: {cond}", "es": "Condicional: {cond}",
        "fr": "Sous condition : {cond}", "ja": "条件付き: {cond}",
        "pt": "Condicional: {cond}"},

    # ---- Progress sheet --------------------------------------------------
    "progress_title": {
        "en": "Reported progress (from meetings)",
        "es": "Avance reportado (según las reuniones)",
        "fr": "Avancement rapporté (d'après les réunions)",
        "ja": "報告された進捗（会議より）",
        "pt": "Progresso relatado (a partir das reuniões)"},
    "progress_note": {
        "en": ("Baseline is the first plan version and never moves. "
               "Every line here is a share of the plan as it stands "
               "today, the baseline included, so a first plan that "
               "covered less work than today's tops out below 100% "
               "instead of finishing at it. "
               "Current plan follows the dates on the {gantt} and "
               "redraws when you edit them. Reported is % complete as of "
               "each meeting, held flat between meetings because that is "
               "all the meetings said, and it stops at the data date. "
               "The vertical line marks the last meeting that reported "
               "progress. Right of that line nothing has reported yet, so "
               "the chart shows plan only, drawn dashed. The dots are the "
               "meetings themselves. This is a meeting-derived progress "
               "curve measured against the plan as it stands today, not "
               "an earned-value S-curve, so adding scope raises what is "
               "left rather than pushing the line back down."),
        "es": ("La línea base es la primera versión del plan y nunca se "
               "mueve. Cada línea aquí es una parte del plan tal como "
               "está hoy, incluida la línea base, así que un primer plan "
               "que cubría menos trabajo que el de hoy se queda por "
               "debajo del 100% en lugar de terminar en él. "
               "El plan actual sigue las fechas de la hoja {gantt} y se "
               "redibuja cuando las editas. Reportado es el % completado "
               "a la fecha de cada reunión, plano entre reuniones porque "
               "eso es todo lo que dijeron las reuniones, y se detiene en "
               "la fecha de datos. La línea vertical marca la última "
               "reunión que reportó avance. A la derecha de esa línea "
               "nadie ha reportado todavía, así que el gráfico muestra "
               "solo el plan, con trazo discontinuo. Los puntos son las "
               "reuniones mismas. Esta es una curva de avance derivada de "
               "las reuniones y medida contra el plan tal como está hoy, "
               "no una curva S de valor ganado, así que agregar alcance "
               "aumenta lo que falta en lugar de empujar la línea hacia "
               "abajo."),
        "fr": ("La référence est la première version du plan et ne bouge "
               "jamais. Chaque courbe ici est une part du plan tel qu'il "
               "est aujourd'hui, référence comprise, donc un premier plan "
               "qui couvrait moins de travail que celui d'aujourd'hui "
               "plafonne sous 100 % au lieu d'y arriver. "
               "Le plan actuel suit les dates de la feuille {gantt} et se "
               "redessine quand tu les modifies. Rapporté est le % "
               "terminé à la date de chaque réunion, maintenu à plat "
               "entre les réunions parce que c'est tout ce que les "
               "réunions ont dit, et il s'arrête à la date des données. "
               "La ligne verticale marque la dernière réunion qui a "
               "rapporté un avancement. À droite de cette ligne, rien n'a "
               "encore été rapporté, donc le graphique montre uniquement "
               "le plan, en pointillés. Les points sont les réunions "
               "elles-mêmes. C'est une courbe d'avancement tirée des "
               "réunions et mesurée par rapport au plan tel qu'il est "
               "aujourd'hui, pas une courbe en S de valeur acquise, donc "
               "ajouter du périmètre augmente ce qui reste au lieu de "
               "faire redescendre la courbe."),
        "ja": ("ベースラインは最初のプランのバージョンで、動くことは"
               "ありません。ここにある線はすべて、ベースラインも含めて、"
               "現在のプランに対する割合です。そのため、最初のプランが"
               "現在より少ない作業しか含んでいなかった場合、100%に届かず"
               "に頭打ちになります。"
               "現在のプランは「{gantt}」シートの日付に従い、日付を編集"
               "すると描き直されます。報告値は各会議時点の完了率で、"
               "会議と会議の間は横ばいです。会議で語られたことがそれだけ"
               "だからです。報告値はデータ日付で止まります。"
               "縦線は進捗が報告された最後の会議を示します。その線より"
               "右はまだ何も報告されていないため、グラフはプランだけを"
               "破線で表示します。点は会議そのものです。"
               "これは会議から得た進捗曲線を現在のプランに照らして測った"
               "もので、アーンドバリューのSカーブではありません。"
               "そのためスコープを追加すると、線が下がるのではなく残りの"
               "作業が増えます。"),
        "pt": ("A linha de base é a primeira versão do plano e nunca se "
               "move. Cada linha aqui é uma parte do plano como ele está "
               "hoje, incluindo a linha de base, então um primeiro plano "
               "que cobria menos trabalho que o de hoje fica abaixo de "
               "100% em vez de terminar nele. "
               "O plano atual segue as datas da aba {gantt} e é "
               "redesenhado quando você edita essas datas. Relatado é o % "
               "concluído na data de cada reunião, mantido estável entre "
               "as reuniões porque isso é tudo o que as reuniões "
               "disseram, e para na data dos dados. A linha vertical "
               "marca a última reunião que relatou progresso. À direita "
               "dessa linha ninguém relatou nada ainda, então o gráfico "
               "mostra só o plano, em tracejado. Os pontos são as "
               "próprias reuniões. Esta é uma curva de progresso derivada "
               "das reuniões e medida em relação ao plano como ele está "
               "hoje, não uma curva S de valor agregado, então adicionar "
               "escopo aumenta o que falta em vez de empurrar a linha "
               "para baixo.")},
    "progress_weighting": {
        "en": ("Weighting: every task counts for its scheduled working "
               "days, which stands in for size. {stated} of {total} tasks "
               "had an effort figure stated in a meeting; none of the "
               "numbers here are weighted by it."),
        "es": ("Ponderación: cada tarea cuenta por sus días hábiles "
               "programados, que hacen las veces de tamaño. {stated} de "
               "{total} tareas tenían un esfuerzo dicho en una reunión; "
               "ninguno de los números de aquí se pondera con él."),
        "fr": ("Pondération : chaque tâche compte pour ses jours ouvrés "
               "planifiés, qui tiennent lieu de taille. {stated} tâches "
               "sur {total} avaient un effort indiqué en réunion ; aucun "
               "des chiffres ici n'est pondéré par cet effort."),
        "ja": ("重み付け: 各タスクは予定された営業日数で数えており、これを"
               "規模の代わりにしています。{total}件中{stated}件のタスクは"
               "会議で工数が語られましたが、ここの数値はどれも工数では"
               "重み付けしていません。"),
        "pt": ("Ponderação: cada tarefa conta pelos dias úteis "
               "programados, que fazem o papel de tamanho. {stated} de "
               "{total} tarefas tinham um esforço falado em reunião; "
               "nenhum dos números aqui é ponderado por ele.")},
    "progress_coverage": {
        "en": "Date coverage: dates on {dated} of {total} activities.",
        "es": ("Cobertura de fechas: fechas en {dated} de {total} "
               "actividades."),
        "fr": ("Couverture des dates : dates sur {dated} des {total} "
               "activités."),
        "ja": "日付のカバー範囲: {total}件中{dated}件のアクティビティに日付あり。",
        "pt": ("Cobertura de datas: datas em {dated} de {total} "
               "atividades.")},
    "progress_coverage_gap": {
        "en": (" Activities the meetings never dated are not on the "
               "timeline and not in these curves."),
        "es": (" Las actividades a las que las reuniones nunca pusieron "
               "fecha no están en la línea de tiempo ni en estas curvas."),
        "fr": (" Les activités que les réunions n'ont jamais datées ne "
               "sont ni sur la chronologie ni dans ces courbes."),
        "ja": ("会議で日付が付かなかったアクティビティは、タイムラインにも"
               "これらの曲線にも含まれません。"),
        "pt": (" As atividades que as reuniões nunca dataram não estão na "
               "linha do tempo nem nestas curvas.")},
    "progress_col_week": {
        "en": "Week ending", "es": "Semana que termina",
        "fr": "Semaine se terminant le", "ja": "週の終了日",
        "pt": "Semana que termina"},
    "progress_col_baseline": {
        "en": "First plan (baseline)", "es": "Primer plan (línea base)",
        "fr": "Premier plan (référence)", "ja": "最初のプラン（ベースライン）",
        "pt": "Primeiro plano (linha de base)"},
    "progress_col_current": {
        "en": "Current plan", "es": "Plan actual", "fr": "Plan actuel",
        "ja": "現在のプラン", "pt": "Plano atual"},
    "progress_col_after": {
        "en": "Plan after data date (planned only)",
        "es": "Plan tras la fecha de datos (solo planificado)",
        "fr": "Plan après la date des données (planifié seulement)",
        "ja": "データ日付より後のプラン（予定のみ）",
        "pt": "Plano após a data dos dados (só planejado)"},
    "progress_col_reported": {
        "en": "Reported (from meetings)",
        "es": "Reportado (según las reuniones)",
        "fr": "Rapporté (d'après les réunions)",
        "ja": "報告値（会議より）",
        "pt": "Relatado (a partir das reuniões)"},
    "progress_col_meeting": {
        "en": "At a meeting", "es": "En una reunión", "fr": "En réunion",
        "ja": "会議時点", "pt": "Em uma reunião"},
    "progress_col_data_date": {
        "en": "Data date", "es": "Fecha de datos", "fr": "Date des données",
        "ja": "データ日付", "pt": "Data dos dados"},
    "progress_baseline_pending": {
        "en": ("Baseline appears once this project has a second plan "
               "version to compare against."),
        "es": ("La línea base aparece cuando este proyecto tenga una "
               "segunda versión del plan con la que comparar."),
        "fr": ("La référence apparaît dès que ce projet a une deuxième "
               "version du plan à comparer."),
        "ja": ("ベースラインは、このプロジェクトに比較できる2つ目のプラン"
               "のバージョンができると表示されます。"),
        "pt": ("A linha de base aparece quando este projeto tiver uma "
               "segunda versão do plano para comparar.")},

    # ---- Slip sheet ------------------------------------------------------
    "slip_title": {
        "en": "Slip, how due dates moved across plan versions",
        "es": "Desvíos, cómo se movieron las fechas límite entre versiones "
              "del plan",
        "fr": "Glissements, comment les échéances ont bougé d'une version "
              "du plan à l'autre",
        "ja": "期日の変動、プランのバージョン間で期日がどう動いたか",
        "pt": "Desvios, como os prazos mudaram entre as versões do plano"},
    "slip_note": {
        "en": ("First committed is the earliest due date anyone stated for "
               "the task, taken from the meetings themselves; where nobody "
               "stated one, it falls back to the earliest generated plan "
               "that tracked the task. Variance is current minus first "
               "committed: positive is late, negative is early. The trail "
               "shows each move, the meeting it came from, and the reason "
               "when somebody gave one."),
        "es": ("Primer compromiso es la fecha límite más temprana que "
               "alguien dijo para la tarea, tomada de las propias "
               "reuniones; si nadie dijo una, se usa el primer plan "
               "generado que siguió la tarea. La variación es la fecha "
               "actual menos el primer compromiso: positiva es tarde, "
               "negativa es antes. El historial muestra cada cambio, la "
               "reunión de la que salió y el motivo cuando alguien dio "
               "uno."),
        "fr": ("Premier engagement est l'échéance la plus ancienne que "
               "quelqu'un a annoncée pour la tâche, tirée des réunions "
               "elles-mêmes ; si personne n'en a annoncé, on prend le "
               "premier plan généré qui suivait la tâche. L'écart est "
               "l'échéance actuelle moins le premier engagement : positif "
               "veut dire en retard, négatif en avance. L'historique "
               "montre chaque changement, la réunion d'où il vient et la "
               "raison quand quelqu'un en a donné une."),
        "ja": ("最初の約束は、そのタスクについて誰かが述べた最も早い期日"
               "で、会議そのものから取っています。誰も述べていない場合は、"
               "そのタスクを追跡した最初の生成プランを使います。差異は"
               "現在の期日から最初の約束を引いたもので、プラスは遅れ、"
               "マイナスは前倒しです。履歴には、それぞれの変更、その元と"
               "なった会議、理由が述べられた場合はその理由が表示されます。"),
        "pt": ("Primeiro compromisso é o prazo mais antigo que alguém "
               "falou para a tarefa, tirado das próprias reuniões; quando "
               "ninguém falou um, vale o primeiro plano gerado que "
               "acompanhou a tarefa. A variação é o prazo atual menos o "
               "primeiro compromisso: positiva é atraso, negativa é "
               "adiantamento. O histórico mostra cada mudança, a reunião "
               "de onde ela veio e o motivo quando alguém deu um.")},
    "slip_col_task": {
        "en": "Task", "es": "Tarea", "fr": "Tâche", "ja": "タスク",
        "pt": "Tarefa"},
    "slip_col_owner": {
        "en": "Owner", "es": "Responsable", "fr": "Responsable",
        "ja": "担当者", "pt": "Responsável"},
    "slip_col_first": {
        "en": "First committed", "es": "Primer compromiso",
        "fr": "Premier engagement", "ja": "最初の約束",
        "pt": "Primeiro compromisso"},
    "slip_col_as_of": {
        "en": "As of", "es": "Con fecha de", "fr": "En date du",
        "ja": "時点", "pt": "Na data de"},
    "slip_col_current": {
        "en": "Current due", "es": "Fecha límite actual",
        "fr": "Échéance actuelle", "ja": "現在の期日", "pt": "Prazo atual"},
    "slip_col_moves": {
        "en": "Times moved", "es": "Veces movida",
        "fr": "Nombre de reports", "ja": "変更回数",
        "pt": "Vezes que mudou"},
    "slip_col_variance": {
        "en": "Variance (days)", "es": "Variación (días)",
        "fr": "Écart (jours)", "ja": "差異（日）", "pt": "Variação (dias)"},
    "slip_col_trail": {
        "en": "Trail", "es": "Historial", "fr": "Historique",
        "ja": "履歴", "pt": "Histórico"},
    "slip_first_tracked": {
        "en": "first tracked now", "es": "seguida desde ahora",
        "fr": "suivie à partir de maintenant", "ja": "今回が初回の追跡",
        "pt": "acompanhada a partir de agora"},
    "slip_stated": {
        "en": "stated", "es": "dicha en reunión", "fr": "annoncée",
        "ja": "会議での発言", "pt": "falada em reunião"},
    "slip_trail_current": {
        "en": "{date} (current)", "es": "{date} (actual)",
        "fr": "{date} (actuelle)", "ja": "{date}（現在）",
        "pt": "{date} (atual)"},
    "slip_trail_said": {
        "en": "{date} (said {said})", "es": "{date} (dicho el {said})",
        "fr": "{date} (annoncé le {said})", "ja": "{date}（{said}の発言）",
        "pt": "{date} (falado em {said})"},
    "slip_nothing_yet": {
        "en": ("Nobody stated a due date more than once in these meetings, "
               "so there is nothing to compare yet: every future gantt for "
               "this project adds a dated version to compare against."),
        "es": ("Nadie dijo una fecha límite más de una vez en estas "
               "reuniones, así que todavía no hay nada que comparar: cada "
               "gantt futuro de este proyecto agrega una versión con "
               "fecha con la que comparar."),
        "fr": ("Personne n'a annoncé une échéance plus d'une fois dans ces "
               "réunions, donc il n'y a encore rien à comparer : chaque "
               "futur gantt de ce projet ajoute une version datée à "
               "comparer."),
        "ja": ("これらの会議では期日を2回以上述べた人がいないため、まだ"
               "比較できるものがありません。今後このプロジェクトでガント"
               "を作るたびに、比較用の日付付きバージョンが追加されます。"),
        "pt": ("Ninguém falou um prazo mais de uma vez nestas reuniões, "
               "então ainda não há nada para comparar: cada gantt futuro "
               "deste projeto adiciona uma versão datada para comparar.")},
    "slip_first_plan": {
        "en": ("This is the first generated plan for this project, so the "
               "trail above is what people said in the meetings. Future "
               "gantts add plan versions to it."),
        "es": ("Este es el primer plan generado para este proyecto, así "
               "que el historial de arriba es lo que la gente dijo en las "
               "reuniones. Los gantts futuros le agregan versiones del "
               "plan."),
        "fr": ("C'est le premier plan généré pour ce projet, donc "
               "l'historique ci-dessus reprend ce que les gens ont dit en "
               "réunion. Les prochains gantts y ajoutent des versions du "
               "plan."),
        "ja": ("これはこのプロジェクトで最初に生成されたプランなので、上の"
               "履歴は会議で人々が述べた内容です。今後のガントでプランの"
               "バージョンが追加されていきます。"),
        "pt": ("Este é o primeiro plano gerado para este projeto, então o "
               "histórico acima é o que as pessoas falaram nas reuniões. "
               "Os próximos gantts adicionam versões do plano a ele.")},

    # ---- Receipts sheet --------------------------------------------------
    "receipts_title": {
        "en": "Receipts, the meeting line behind every value",
        "es": "Evidencias, la frase de la reunión detrás de cada valor",
        "fr": "Justificatifs, la phrase de réunion derrière chaque valeur",
        "ja": "根拠、それぞれの値の裏付けとなる会議での発言",
        "pt": "Evidências, a fala da reunião por trás de cada valor"},
    "receipts_note": {
        "en": ("The plan is generated from the meetings, so provenance is "
               "automatic. Quotes are verbatim from the source content."),
        "es": ("El plan se genera a partir de las reuniones, así que la "
               "procedencia es automática. Las citas son literales del "
               "contenido original."),
        "fr": ("Le plan est généré à partir des réunions, donc la "
               "provenance est automatique. Les citations sont reprises "
               "mot pour mot du contenu source."),
        "ja": ("プランは会議から生成されるため、出どころは自動的に"
               "記録されます。引用は元の内容そのままです。"),
        "pt": ("O plano é gerado a partir das reuniões, então a "
               "procedência é automática. As citações são literais do "
               "conteúdo original.")},
    "receipts_forwarding": {
        "en": ("Forwarding note: this sheet quotes people by name. "
               "Consider removing it before sending the workbook outside "
               "the team."),
        "es": ("Nota para reenviar: esta hoja cita a personas por su "
               "nombre. Considera quitarla antes de enviar el libro fuera "
               "del equipo."),
        "fr": ("Note avant transfert : cette feuille cite des personnes "
               "par leur nom. Pense à la retirer avant d'envoyer le "
               "classeur en dehors de l'équipe."),
        "ja": ("転送時の注意: このシートは人の発言を名前付きで引用して"
               "います。ブックをチームの外に送る前に、削除を検討して"
               "ください。"),
        "pt": ("Nota de encaminhamento: esta aba cita pessoas pelo nome. "
               "Pense em remover antes de mandar a planilha para fora do "
               "time.")},
    "receipts_col_ref": {
        "en": "Ref", "es": "Ref.", "fr": "Réf.", "ja": "参照", "pt": "Ref."},
    "receipts_col_task": {
        "en": "Task", "es": "Tarea", "fr": "Tâche", "ja": "タスク",
        "pt": "Tarefa"},
    "receipts_col_supports": {
        "en": "Supports", "es": "Respalda", "fr": "Justifie",
        "ja": "裏付ける項目", "pt": "Sustenta"},
    "receipts_col_meeting": {
        "en": "Meeting", "es": "Reunión", "fr": "Réunion", "ja": "会議",
        "pt": "Reunião"},
    "receipts_col_standing": {
        "en": "Standing", "es": "Vigencia", "fr": "Validité",
        "ja": "有効性", "pt": "Vigência"},
    "receipts_col_speaker": {
        "en": "Speaker", "es": "Quién lo dijo", "fr": "Intervenant",
        "ja": "発言者", "pt": "Quem falou"},
    "receipts_col_quote": {
        "en": "Verbatim line", "es": "Frase literal",
        "fr": "Phrase mot pour mot", "ja": "発言そのまま",
        "pt": "Fala literal"},
    "standing_current": {
        "en": "current", "es": "vigente", "fr": "en vigueur",
        "ja": "有効", "pt": "vigente"},
    "standing_superseded": {
        "en": "superseded", "es": "reemplazada", "fr": "remplacée",
        "ja": "置き換え済み", "pt": "substituída"},
    "field_due": {
        "en": "Due date", "es": "Fecha límite", "fr": "Échéance",
        "ja": "期日", "pt": "Prazo"},
    "field_start": {
        "en": "Start date", "es": "Fecha de inicio", "fr": "Date de début",
        "ja": "開始日", "pt": "Data de início"},
    "field_owner": {
        "en": "Owner", "es": "Responsable", "fr": "Responsable",
        "ja": "担当者", "pt": "Responsável"},
    "field_status": {
        "en": "Status", "es": "Estado", "fr": "Statut", "ja": "ステータス",
        "pt": "Status"},
    "field_effort": {
        "en": "Effort estimate", "es": "Estimación de esfuerzo",
        "fr": "Estimation de l'effort", "ja": "工数の見積もり",
        "pt": "Estimativa de esforço"},
    "field_progress": {
        "en": "Progress", "es": "Avance", "fr": "Avancement", "ja": "進捗",
        "pt": "Progresso"},
    "field_dependency": {
        "en": "Dependency", "es": "Dependencia", "fr": "Dépendance",
        "ja": "依存関係", "pt": "Dependência"},
    "field_task": {
        "en": "Task", "es": "Tarea", "fr": "Tâche", "ja": "タスク",
        "pt": "Tarefa"},
    "field_condition": {
        "en": "Condition", "es": "Condición", "fr": "Condition",
        "ja": "条件", "pt": "Condição"},

    # ---- contract workbooks (workbook_plan.py) ---------------------------
    "wb_sheet_default": {
        "en": "Sheet", "es": "Hoja", "fr": "Feuille", "ja": "シート",
        "pt": "Planilha"},
    "wb_total": {
        "en": "Total", "es": "Total", "fr": "Total", "ja": "合計",
        "pt": "Total"},
    "wb_readme_tab": {
        "en": "README", "es": "LÉEME", "fr": "LISEZ-MOI", "ja": "はじめに",
        "pt": "LEIA-ME"},
    "wb_title_default": {
        "en": "Workbook", "es": "Libro", "fr": "Classeur", "ja": "ブック",
        "pt": "Planilha"},
    "wb_purpose": {
        "en": "Purpose", "es": "Propósito", "fr": "Objectif", "ja": "目的",
        "pt": "Objetivo"},
    "wb_scope": {
        "en": "Scope", "es": "Alcance", "fr": "Périmètre", "ja": "範囲",
        "pt": "Escopo"},
    "wb_how_to_read": {
        "en": "How to read", "es": "Cómo leerlo", "fr": "Comment lire",
        "ja": "読み方", "pt": "Como ler"},
    "wb_reviewers": {
        "en": "Reviewers", "es": "Revisores", "fr": "Relecteurs",
        "ja": "レビュー担当", "pt": "Revisores"},
    "wb_handoff": {
        "en": "Handoff", "es": "Traspaso", "fr": "Passation",
        "ja": "引き継ぎ", "pt": "Repasse"},
}

# Contract column headers and titles (artifact_types.py), keyed by the
# ENGLISH text the contract declares. The contract stays the single place a
# column is defined (its label is also quoted in the prompt the model
# reads), and this table only says how that header reads in each language.
# A label with no entry renders in English, and a test lists every one.
_LABELS: dict[str, dict[str, str]] = {
    "Scenario ID": {"es": "ID del escenario", "fr": "ID du scénario",
                    "ja": "シナリオID", "pt": "ID do cenário"},
    "Scenario": {"es": "Escenario", "fr": "Scénario", "ja": "シナリオ",
                 "pt": "Cenário"},
    "Type": {"es": "Tipo", "fr": "Type", "ja": "種別", "pt": "Tipo"},
    "Preconditions": {"es": "Precondiciones", "fr": "Préconditions",
                      "ja": "前提条件", "pt": "Pré-condições"},
    "Sample Test Data": {"es": "Datos de prueba de ejemplo",
                         "fr": "Exemple de données de test",
                         "ja": "テストデータ例",
                         "pt": "Dados de teste de exemplo"},
    "Expected Behavior": {"es": "Comportamiento esperado",
                          "fr": "Comportement attendu",
                          "ja": "期待される動作",
                          "pt": "Comportamento esperado"},
    "Notes for Dev": {"es": "Notas para desarrollo",
                      "fr": "Notes pour les devs", "ja": "開発向けメモ",
                      "pt": "Notas para o dev"},
    "Source": {"es": "Fuente", "fr": "Source", "ja": "出典", "pt": "Fonte"},
    "ID": {"es": "ID", "fr": "ID", "ja": "ID", "pt": "ID"},
    "Action": {"es": "Acción", "fr": "Action", "ja": "アクション",
               "pt": "Ação"},
    "Owner": {"es": "Responsable", "fr": "Responsable", "ja": "担当者",
              "pt": "Responsável"},
    "Due": {"es": "Fecha límite", "fr": "Échéance", "ja": "期限",
            "pt": "Prazo"},
    "Status": {"es": "Estado", "fr": "Statut", "ja": "ステータス",
               "pt": "Status"},
    "Blocked By": {"es": "Bloqueado por", "fr": "Bloqué par",
                   "ja": "ブロック要因", "pt": "Bloqueado por"},
    "Notes": {"es": "Notas", "fr": "Notes", "ja": "メモ", "pt": "Notas"},
    "Decision": {"es": "Decisión", "fr": "Décision", "ja": "決定事項",
                 "pt": "Decisão"},
    "Date": {"es": "Fecha", "fr": "Date", "ja": "日付", "pt": "Data"},
    "Decided By": {"es": "Decidido por", "fr": "Décidé par", "ja": "決定者",
                   "pt": "Decidido por"},
    "Why": {"es": "Por qué", "fr": "Pourquoi", "ja": "理由",
            "pt": "Por quê"},
    "Rejected": {"es": "Descartado", "fr": "Écarté", "ja": "却下した案",
                 "pt": "Descartado"},
    "Reversible": {"es": "Reversible", "fr": "Réversible",
                   "ja": "取り消し可否", "pt": "Reversível"},
    "Affects": {"es": "Afecta a", "fr": "Concerne", "ja": "影響範囲",
                "pt": "Afeta"},
    "Risk": {"es": "Riesgo", "fr": "Risque", "ja": "リスク", "pt": "Risco"},
    "Category": {"es": "Categoría", "fr": "Catégorie", "ja": "カテゴリ",
                 "pt": "Categoria"},
    "Likelihood": {"es": "Probabilidad", "fr": "Probabilité",
                   "ja": "発生可能性", "pt": "Probabilidade"},
    "Impact": {"es": "Impacto", "fr": "Impact", "ja": "影響度",
               "pt": "Impacto"},
    "Severity": {"es": "Severidad", "fr": "Gravité", "ja": "深刻度",
                 "pt": "Severidade"},
    "Mitigation": {"es": "Mitigación", "fr": "Atténuation", "ja": "対策",
                   "pt": "Mitigação"},
    "Early Warning": {"es": "Señal temprana", "fr": "Signe avant-coureur",
                      "ja": "早期の兆候", "pt": "Sinal de alerta"},
    "Question": {"es": "Pregunta", "fr": "Question", "ja": "質問",
                 "pt": "Pergunta"},
    "Raised By": {"es": "Planteada por", "fr": "Soulevée par",
                  "ja": "提起者", "pt": "Levantada por"},
    "Blocks": {"es": "Bloquea", "fr": "Bloque", "ja": "ブロック対象",
               "pt": "Bloqueia"},
    "Who Can Answer": {"es": "Quién puede responder",
                       "fr": "Qui peut répondre", "ja": "回答できる人",
                       "pt": "Quem pode responder"},
    "Needed By": {"es": "Se necesita para", "fr": "Nécessaire pour le",
                  "ja": "必要な期日", "pt": "Necessária até"},
    "Requirement": {"es": "Requisito", "fr": "Exigence", "ja": "要件",
                    "pt": "Requisito"},
    "Priority": {"es": "Prioridad", "fr": "Priorité", "ja": "優先度",
                 "pt": "Prioridade"},
    "Acceptance Criteria": {"es": "Criterios de aceptación",
                            "fr": "Critères d'acceptation",
                            "ja": "受け入れ基準",
                            "pt": "Critérios de aceite"},
    "Heard As": {"es": "Dicho como", "fr": "Formulé ainsi",
                 "ja": "元の発言", "pt": "Dito como"},
    "Criterion": {"es": "Criterio", "fr": "Critère", "ja": "評価基準",
                  "pt": "Critério"},
    "Weight": {"es": "Peso", "fr": "Poids", "ja": "重み", "pt": "Peso"},
    "Line Item": {"es": "Partida", "fr": "Poste", "ja": "項目",
                  "pt": "Item"},
    "Basis": {"es": "Base", "fr": "Base", "ja": "算出根拠", "pt": "Base"},
    "Low": {"es": "Mínimo", "fr": "Bas", "ja": "下限", "pt": "Mínimo"},
    "High": {"es": "Máximo", "fr": "Haut", "ja": "上限", "pt": "Máximo"},
    "Spread": {"es": "Rango", "fr": "Écart", "ja": "幅", "pt": "Variação"},
    "Confidence": {"es": "Confianza", "fr": "Confiance", "ja": "確度",
                   "pt": "Confiança"},
    "Topic": {"es": "Tema", "fr": "Sujet", "ja": "トピック", "pt": "Tema"},
    "First Raised": {"es": "Primera mención", "fr": "Première mention",
                     "ja": "初出", "pt": "Primeira menção"},
    "Last Discussed": {"es": "Última discusión",
                       "fr": "Dernière discussion", "ja": "最終議論",
                       "pt": "Última discussão"},
    "Meetings In Memory": {"es": "Reuniones en memoria",
                           "fr": "Réunions en mémoire",
                           "ja": "メモリ内の会議数",
                           "pt": "Reuniões na memória"},
    "Where It Stands": {"es": "Situación actual", "fr": "Où ça en est",
                        "ja": "現状", "pt": "Situação atual"},
    "Days Since Last": {"es": "Días desde la última",
                        "fr": "Jours depuis la dernière",
                        "ja": "最終議論からの日数",
                        "pt": "Dias desde a última"},
    "Still Unresolved": {"es": "Sigue sin resolver",
                         "fr": "Toujours non résolu", "ja": "未解決",
                         "pt": "Ainda sem solução"},
    # contract titles (the cover sheet title when the model sends none)
    "Test scenario plan": {"es": "Plan de escenarios de prueba",
                           "fr": "Plan de scénarios de test",
                           "ja": "テストシナリオ計画",
                           "pt": "Plano de cenários de teste"},
    "Action items and commitments": {"es": "Acciones y compromisos",
                                     "fr": "Actions et engagements",
                                     "ja": "アクションアイテムと約束事項",
                                     "pt": "Ações e compromissos"},
    "Decision log": {"es": "Registro de decisiones",
                     "fr": "Journal des décisions", "ja": "決定ログ",
                     "pt": "Registro de decisões"},
    "Risk register": {"es": "Registro de riesgos",
                      "fr": "Registre des risques", "ja": "リスク登録簿",
                      "pt": "Registro de riscos"},
    "Open questions and blockers": {"es": "Preguntas abiertas y bloqueos",
                                    "fr": "Questions ouvertes et blocages",
                                    "ja": "未解決の質問とブロッカー",
                                    "pt": "Perguntas em aberto e bloqueios"},
    "Requirements and scope": {"es": "Requisitos y alcance",
                               "fr": "Exigences et périmètre",
                               "ja": "要件とスコープ",
                               "pt": "Requisitos e escopo"},
    "Option comparison": {"es": "Comparación de opciones",
                          "fr": "Comparaison des options",
                          "ja": "選択肢の比較",
                          "pt": "Comparação de opções"},
    "Budget and cost estimate": {"es": "Presupuesto y estimación de costos",
                                 "fr": "Budget et estimation des coûts",
                                 "ja": "予算とコスト見積もり",
                                 "pt": "Orçamento e estimativa de custos"},
    "Cross-meeting topic tracker": {
        "es": "Seguimiento de temas entre reuniones",
        "fr": "Suivi des sujets entre réunions",
        "ja": "会議横断トピックトラッカー",
        "pt": "Acompanhamento de temas entre reuniões"},
}

# The table keyed by locale, which is the shape everything reads.
STRINGS: dict[str, dict[str, str]] = {
    loc: {key: by_loc[loc] for key, by_loc in _BY_KEY.items()
          if loc in by_loc}
    for loc in LOCALES
}
LABELS: dict[str, dict[str, str]] = {
    loc: {en: (en if loc == "en" else by_loc[loc])
          for en, by_loc in _LABELS.items() if loc == "en" or loc in by_loc}
    for loc in LOCALES
}

# Receipts "Supports" cell: schema field -> string key.
_FIELD_KEYS = {
    "end": "field_due", "end_date": "field_due", "due": "field_due",
    "start": "field_start", "start_date": "field_start",
    "owner": "field_owner", "status": "field_status",
    "effort": "field_effort", "percent_complete": "field_progress",
    "percent": "field_progress", "depends_on": "field_dependency",
    "name": "field_task", "conditional": "field_condition",
}

# Japanese is not in share_card's month and weekday tables (its dates are
# numeric, "7月20日"), so the one thing it needs here is the day letters.
_JA_DAY_LETTERS = "月火水木金土日"


def resolve_locale(locale: str | None) -> str:
    """The table locale for a request locale: "pt-BR" and "pt_br" are pt,
    anything we do not carry (and no locale at all) is English."""
    loc = normalize_locale(locale)
    base = loc.split("-", 1)[0] if loc else DEFAULT_LOCALE
    return base if base in LOCALES else DEFAULT_LOCALE


class DocStrings:
    """The strings and date formats for one resolved locale."""

    def __init__(self, locale: str | None = None) -> None:
        self.locale = resolve_locale(locale)
        self._table = STRINGS[self.locale]

    def t(self, key: str, **fmt) -> str:
        """The string for `key`, English when this locale lacks it."""
        text = self._table.get(key)
        if text is None:
            text = STRINGS[DEFAULT_LOCALE][key]
        return text.format(**fmt) if fmt else text

    def label(self, english: str) -> str:
        """A contract column header or title, English when untranslated."""
        return LABELS[self.locale].get(english, english)

    # ---- load-bearing groups --------------------------------------------
    def sheet_names(self) -> dict[str, str]:
        return {k: self.t(k) for k in SHEET_KEYS}

    def status_labels(self) -> dict[str, str]:
        return {k: self.t(f"status_{k}") for k in STATUS_KEYS}

    def field_label(self, field: str) -> str:
        """Receipts is a customer-facing sheet: our schema keys are not.
        Anything unmapped falls back to the raw key made readable rather
        than dropped, so a new schema field surfaces as odd wording, never
        as a blank cell."""
        key = str(field or "").strip()
        mapped = _FIELD_KEYS.get(key.lower())
        return (self.t(mapped) if mapped
                else key.replace("_", " ").capitalize())

    # ---- dates ----------------------------------------------------------
    def short_date(self, d: date, *, pad: bool = True) -> str:
        """Month and day, the way this locale writes it: "Jul 21",
        "21 jul", "21 juil", "7月21日". `pad` only affects English, which
        has always zero-padded the strip and the slip trail ("Jul 06") and
        not the chart's week labels ("Jul 6")."""
        if self.locale == "ja":
            return f"{d.month}月{d.day}日"
        from app.services.share_card import MONTHS
        month = MONTHS[self.locale][d.month - 1]
        if self.locale == "en":
            return f"{month} {d.day:02d}" if pad else f"{month} {d.day}"
        return f"{d.day} {month}"

    def day_letter(self, weekday: int) -> str:
        """One letter per weekday for the grid header, Monday is 0."""
        if self.locale == "ja":
            return _JA_DAY_LETTERS[weekday]
        from app.services.share_card import DAYS
        return DAYS[self.locale][weekday][0].upper()
