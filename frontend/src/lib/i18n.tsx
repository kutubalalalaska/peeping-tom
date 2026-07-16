// Lightweight, dependency-free i18n. One source-of-truth English dictionary; the
// Russian/Italian maps are typed against its keys, so a missing translation is a
// COMPILE error (and falls back to English at runtime as a belt-and-braces).
//
// The chosen language drives BOTH the UI copy and — carried to the backend on
// upload — the language the frontier read comes back in. It is deliberately
// independent of Whisper: voice notes are still transcribed in whatever tongue
// they were spoken; only the analysis prose follows this choice.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type Lang = "en" | "ru" | "it";
export const LANGS: Lang[] = ["en", "ru", "it"];
const STORAGE_KEY = "mirror_lang";

// ---- dictionary (English = source of truth) ----
const en = {
  "common.back": "[ ← back ]",

  "landing.hero": "Please upload your chat.",
  "landing.blurb":
    "This demonstration has a zero-retention policy. We process the media locally and we use open source LLM providers to analyze the conversations. Once the demonstration is over you can delete your data, otherwise it will be automatically erased no later than in 24 hours. Please enjoy the demonstration",
  "landing.begin": "[ begin → ]",
  "landing.quotaLeft": "{remaining} of {limit} reads left today",
  "landing.quotaNone":
    "you've used all {limit} reads for today — they reset within a day",
  "landing.source": "Source",
  "landing.dataCycle": "Data cycle",

  "start.step1": "step 1/4 · platform",
  "start.step2": "step 2/4 · export",
  "start.selectPlatform": "select the platform",
  "start.exportFrom": "export from {platform}",
  "start.thenDrop": "…then drop the .zip below.",
  "start.chooseZip": "choose .zip",
  "start.uploading": "uploading…",
  "start.uploadBtn": "upload .zip →",
  "start.consent":
    "this is my own conversation and it contains no illegal content.",
  "start.errRate": "You've reached your reads for now. Try again later.",
  "start.errTooLarge": "That chat export is too large to upload here.",
  "start.errWrongPlatform":
    "this looks like a {found} export, but {selected} is selected — go back and pick {found}.",
  "start.errNotExport":
    "this doesn't look like a {platform} export — no chat file inside the zip. upload the .zip exactly as {platform} made it.",
  "start.bgPause": "uploads pause while this tab is in the background — keep it visible until done.",
  "start.wa.iphone": [
    "open the chat in whatsapp.",
    "tap the contact / group name at the top.",
    "scroll down → “export chat”.",
    "choose “attach media” — whatsapp makes a .zip.",
  ],
  "start.wa.android": [
    "open the chat in whatsapp.",
    "tap ⋮ (top-right) → more → “export chat”.",
    "choose “include media” — whatsapp makes a .zip.",
  ],
  "start.mobileHint":
    "on a phone this is a couple of extra taps — it's smoothest on a computer, but it works here too.",
  "start.handoff.ios":
    "in the share sheet, tap “Save to Files”, then tap “choose .zip” below and pick that file.",
  "start.handoff.android":
    "save the .zip to Files or Downloads, then tap “choose .zip” below and pick it.",
  "start.handoff.desktop":
    "exported on your phone? airdrop, email, or save the .zip to this computer — then choose it below.",
  "start.tgMobile.title": "you'll need a computer for telegram",
  "start.tgMobile.body":
    "telegram only exports chats from Telegram Desktop — it can't be done on a phone. open peeping-tom.com on a laptop or desktop, or pick a whatsapp chat instead.",
  "start.tg": [
    "use telegram desktop from desktop.telegram.org — only this build does the JSON export.",
    "open the chat → ⋮ → “export chat history”.",
    "set format to “machine-readable JSON” — NOT html.",
    "tick photos, voice & video messages, and stickers.",
    "zip the exported folder, drop the .zip below.",
  ],

  "insp.step3upload": "step 3/4 · upload",
  "start.mode.fast": "[ fast · text-first ]",
  "start.mode.deep": "[ deep · full decode ]",
  "start.mode.fastSub":
    "reads the text first, then opens only the media it decides it needs — the quickest read.",
  "start.mode.deepSub":
    "decodes every photo and voice note while it reads, folding them in as evidence — the fullest read, slower.",
  "insp.requestsTitle": "what the read asked to open",
  "insp.requestingHero": "choosing what to open",
  "insp.decodingReqHero": "Clarifying some images",
  "insp.foldingHero": "revising with new evidence",
  "insp.foldRound": "revising the read · round {round}",
  "insp.bgDecode": "decoding media in the background  {done}/{total}",
  "slice.tooBig": "this export is {size} — over the {cap} upload limit. ",
  "slice.tooLong": "this chat is longer than one read can hold coherently. ",
  "slice.reading": "reading it on this device to offer a cut — nothing uploads.",
  "slice.pick": "keep a part of the conversation — cut it right here, locally:",
  "slice.latest": "[ the latest part ]",
  "slice.earliest": "[ the earliest part ]",
  "slice.middle": "[ the middle ]",
  "slice.window": "window",
  "slice.unitTokens": "tokens",
  "slice.selected": "{n} messages · {range}",
  "slice.fits": "fits ✓",
  "slice.over": "still too large ✗",
  "slice.cut": "[ cut & continue → ]",
  "slice.cancel": "[ pick another file ]",
  "slice.building": "packing the slice — {done}/{total} files…",
  "slice.failed": "couldn't read this zip here — try a smaller export.",
  "slice.slicedName": "sliced:",
  "result.sliceProv":
    "this read covers {range} — a slice you cut locally from a larger export",
  "insp.step3parse": "step 3/4 · parse",
  "insp.step4": "step 4/4 · the read",
  "insp.errorStep": "error",
  "insp.errorHero": "something broke",
  "insp.configStep": "config",
  "insp.configHero": "no read route",
  "insp.configHint": "set a read route (or ROUTE_A_PROVIDER=mock) and retry.",
  "insp.readingFallback": "the model is reading the transcript…",
  "insp.readingChat": "reading your chat",
  "insp.justDecoded": "just decoded",
  "insp.thinking": "thinking…",
  "insp.composing": "composing your read…",
  "insp.uploadingHero": "uploading your chat",
  "insp.parsingHero": "parsing your chat",
  "insp.uploadingBody1": "sending chat.zip to this machine…",
  "insp.uploadingBody2": "the raw file stays local",
  "insp.custodyLocal": "processed on this machine — nothing has left it",
  "insp.parsingFallback": "parsing your chat…",
  "insp.parsingBody": "reading messages and transcribing voice notes — locally",
  "insp.phaseReinspect": "re-checking",
  "insp.parsingShort": "parsing…",
  "insp.tips": [
    "the model reads what's implicit",
    "patterns surface across time",
    "a long history can take a few minutes to read.",
  ],
  "insp.etaLeft": "~{eta} left",

  "result.loadingHero": "loading the read",
  "result.hero": "the read",
  "result.destroyedHero": "self-destructed",
  "result.destroyedBody": [
    "this read has self-destructed.",
    "",
    "the transcript, the media, and the read —",
    "all deleted automatically. nothing remains.",
  ],
  "result.startOver": "start over",
  "result.startOverSub": "upload another chat for a fresh read",
  "result.selfDestructIn": "this read self-destructs in",
  "result.selfDestructSub":
    "then the transcript, media, and read are deleted automatically — or nuke it now",
  "result.deepProv1":
    "the model asked for a closer look at {n} photo, then re-read with them in view.",
  "result.deepProvN":
    "the model asked for a closer look at {n} photos, then re-read with them in view.",
  "result.readByRoute":
    "read by {model} · via the {route} route",
  "result.readByNoRoute":
    "read by {model}",
  "result.theModel": "the model",
  "result.provoke":
    "this is how a frontier ai model profiled you — for good, or for bad.",
  "result.viewText": "view the exact text that was sent →",
  "result.heldNow": "",
  "result.heldRawMedia": "",
  "result.heldTranscript": "",
  "result.heldRead": "",
  "result.heldNone": "—",
  "result.nukeBtn": "nuke all my data",
  "result.nukeSub":
    "deletes all your data",
  "result.nukeSteps": [
    "> nuke --all",
    "purging raw media ........ gone",
    "purging transcript ....... gone",
    "purging the read ......... gone",
    "purging this session ..... gone",
    "",
    "Your data is deleted. starting over…",
  ],

  // DataFlowModal — the custody-animation explainer
  "df.head": "how your data is processed",
  "df.h1": "Here is how we process your data",
  "df.laneMessages": "messages →",
  "df.laneTranscript": "← transcript",
  "df.you": "YOU",
  "df.youSub": "This is your device",
  "df.serverHosted": "OUR SERVER",
  "df.serverLocal": "YOUR MACHINE",
  "df.serverSubHosted": "our website",
  "df.serverSubLocal": "local",
  "df.orVps": "YOUR VPS",
  "df.llm": "llm",
  "df.noRetentionSub": " · no retention",
  "df.trashLabel1": "images + messages",
  "df.trashLabel2": "destroyed",
  "df.readObj": "read",
  "df.clab": "parsing images · stays {where}",
  "df.whereClab.server": "on our server",
  "df.whereClab.local": "on your machine",
  "df.where.server": "on the server",
  "df.where.local": "on this machine",
  "df.yourVps": "your vps",
  "df.theModel": "the model",
  "df.noRetention": " — no retention",
  "df.caps": [
    "a cartoon cat covering its face",
    "two people at a table",
    "a screenshot of an app",
    "a blurred street at night",
    "a plate of food, from above",
    "a dog mid-jump on grass",
  ],
  "df.foot.you": "this is you, with your exported chat.",
  "df.foot.onlineHosted": "our server comes online.",
  "df.foot.onlineLocal": "your machine does the work.",
  "df.foot.upload": "you upload — the .zip is read {where}.",
  "df.foot.decode": "images are decoded {where}. ",
  "df.foot.send": "only the text transcript goes to {model} via {provider}{ret}.",
  "df.foot.analysis": "analysis complete.",
  "df.foot.comeback": "the read comes back to you.",
  "df.foot.destroy": "the raw images & messages are destroyed — nothing remains.",

  // chat bubble + context drawer
  "bubble.blindCaption": "— blind caption",
  "bubble.openInChat": "open in chat",
  "drawer.title": "your chat",
  "drawer.close": "close",
  "drawer.loading": "loading the chat…",
  "drawer.deleted": "this chat is no longer available — it was deleted.",
  "drawer.earlier": "↑ earlier messages",
  "drawer.later": "↓ later messages",
};

export type MsgKey = keyof typeof en;
export type TFunc = (key: MsgKey, vars?: Record<string, string | number>) => string;
type Dict = Record<MsgKey, string | string[]>;

const ru: Dict = {
  "common.back": "[ ← назад ]",

  "landing.hero": "Пожалуйста, загрузите ваш чат.",
  "landing.blurb":
    "Эта демонстрация работает по принципу нулевого хранения данных. Медиа обрабатываются локально, а для анализа переписки используются open-source LLM-провайдеры. После окончания демонстрации вы можете удалить свои данные — иначе они будут автоматически стёрты не позднее чем через 24 часа. Приятного знакомства с демонстрацией",
  "landing.begin": "[ начать → ]",
  "landing.quotaLeft": "осталось {remaining} из {limit} разборов на сегодня",
  "landing.quotaNone":
    "вы использовали все {limit} разборов на сегодня — они обновятся в течение суток",
  "landing.source": "Исходный код",
  "landing.dataCycle": "Цикл данных",

  "start.step1": "шаг 1/4 · платформа",
  "start.step2": "шаг 2/4 · экспорт",
  "start.selectPlatform": "выберите платформу",
  "start.exportFrom": "экспорт из {platform}",
  "start.thenDrop": "…затем перетащите .zip ниже.",
  "start.chooseZip": "выбрать .zip",
  "start.uploading": "загрузка…",
  "start.uploadBtn": "загрузить .zip →",
  "start.consent":
    "это моя собственная переписка, и она не содержит незаконного контента.",
  "start.errRate":
    "Вы исчерпали лимит разборов на сейчас. Попробуйте позже.",
  "start.errTooLarge": "Этот экспорт чата слишком большой для загрузки здесь.",
  "start.errWrongPlatform":
    "похоже, это экспорт из {found}, а выбран {selected} — вернитесь и выберите {found}.",
  "start.errNotExport":
    "это не похоже на экспорт из {platform} — в архиве нет файла чата. загрузите .zip ровно таким, каким его создал {platform}.",
  "start.bgPause": "загрузка приостанавливается, пока вкладка в фоне — держите её открытой до конца.",
  "start.wa.iphone": [
    "откройте чат в whatsapp.",
    "нажмите на имя контакта / группы вверху.",
    "прокрутите вниз → «экспорт чата».",
    "выберите «прикрепить медиа» — whatsapp создаст .zip.",
  ],
  "start.wa.android": [
    "откройте чат в whatsapp.",
    "нажмите ⋮ (вверху справа) → ещё → «экспорт чата».",
    "выберите «добавить медиа» — whatsapp создаст .zip.",
  ],
  "start.mobileHint":
    "на телефоне это пара лишних шагов — удобнее на компьютере, но и здесь работает.",
  "start.handoff.ios":
    "в меню «Поделиться» нажмите «Сохранить в Файлы», затем нажмите «выбрать .zip» ниже и выберите этот файл.",
  "start.handoff.android":
    "сохраните .zip в Файлы или Загрузки, затем нажмите «выбрать .zip» ниже и выберите его.",
  "start.handoff.desktop":
    "экспортировали на телефоне? перешлите .zip на этот компьютер (airdrop, почта, диск) — затем выберите его ниже.",
  "start.tgMobile.title": "для telegram понадобится компьютер",
  "start.tgMobile.body":
    "telegram экспортирует чаты только из Telegram Desktop — на телефоне это невозможно. откройте peeping-tom.com на ноутбуке или компьютере, либо выберите чат whatsapp.",
  "start.tg": [
    "используйте telegram desktop с desktop.telegram.org — только эта версия делает экспорт в JSON.",
    "откройте чат → ⋮ → «экспортировать историю чата».",
    "выберите формат «машиночитаемый JSON» — НЕ html.",
    "отметьте фото, голосовые и видеосообщения и стикеры.",
    "заархивируйте экспортированную папку, перетащите .zip ниже.",
  ],

  "insp.step3upload": "шаг 3/4 · загрузка",
  "start.mode.fast": "[ быстрый · сначала текст ]",
  "start.mode.deep": "[ глубокий · всё медиа ]",
  "start.mode.fastSub":
    "сначала читает текст, затем открывает только те медиа, которые сочтёт важными — самый быстрый разбор.",
  "start.mode.deepSub":
    "декодирует каждое фото и голосовое, пока читает, вплетая их как доказательства — самый полный разбор, дольше.",
  "insp.requestsTitle": "что разбор попросил открыть",
  "insp.requestingHero": "выбирает, что открыть",
  "insp.decodingReqHero": "открываю то, что попросил разбор",
  "insp.foldingHero": "пересматриваю с новыми данными",
  "insp.foldRound": "пересматриваю разбор · раунд {round}",
  "insp.bgDecode": "декодирую медиа в фоне  {done}/{total}",
  "slice.tooBig": "этот экспорт весит {size} — больше лимита загрузки {cap}. ",
  "slice.tooLong": "эта переписка длиннее, чем один разбор может связно охватить. ",
  "slice.reading": "читаю его на этом устройстве, чтобы предложить вырезку — ничего не загружается.",
  "slice.pick": "оставьте часть переписки — вырежьте её прямо здесь, локально:",
  "slice.latest": "[ последняя часть ]",
  "slice.earliest": "[ самая ранняя ]",
  "slice.middle": "[ середина ]",
  "slice.window": "окно",
  "slice.unitTokens": "токенов",
  "slice.selected": "{n} сообщений · {range}",
  "slice.fits": "помещается ✓",
  "slice.over": "всё ещё слишком много ✗",
  "slice.cut": "[ вырезать и продолжить → ]",
  "slice.cancel": "[ выбрать другой файл ]",
  "slice.building": "собираю вырезку — {done}/{total} файлов…",
  "slice.failed": "не получилось прочитать этот zip здесь — попробуйте экспорт поменьше.",
  "slice.slicedName": "вырезка:",
  "result.sliceProv":
    "этот разбор покрывает {range} — вырезку, которую вы сделали локально из большего экспорта; ничего за её пределами не загружалось.",
  "insp.step3parse": "шаг 3/4 · чтение",
  "insp.step4": "шаг 4/4 · разбор",
  "insp.errorStep": "ошибка",
  "insp.errorHero": "что-то сломалось",
  "insp.configStep": "конфигурация",
  "insp.configHero": "нет маршрута для разбора",
  "insp.configHint":
    "задайте маршрут разбора (или ROUTE_A_PROVIDER=mock) и повторите.",
  "insp.readingFallback": "модель читает транскрипт…",
  "insp.readingChat": "читаю ваш чат",
  "insp.justDecoded": "только что декодировано",
  "insp.thinking": "думаю…",
  "insp.composing": "составляю ваш разбор…",
  "insp.uploadingHero": "загружаю ваш чат",
  "insp.parsingHero": "разбираю ваш чат",
  "insp.uploadingBody1": "отправляю chat.zip на эту машину…",
  "insp.uploadingBody2": "исходный файл остаётся локально",
  "insp.custodyLocal": "обработано на этой машине — ничего её не покинуло",
  "insp.parsingFallback": "разбираю ваш чат…",
  "insp.parsingBody":
    "читаю сообщения и расшифровываю голосовые — локально",
  "insp.phaseReinspect": "перепроверка",
  "insp.parsingShort": "разбор…",
  "insp.tips": [
    "модель читает то, что подразумевается",
    "закономерности проявляются во времени",
    "долгую историю модель может читать несколько минут.",
  ],
  "insp.etaLeft": "~{eta} осталось",

  "result.loadingHero": "загружаю разбор",
  "result.hero": "разбор",
  "result.destroyedHero": "самоуничтожен",
  "result.destroyedBody": [
    "этот разбор самоуничтожился.",
    "",
    "транскрипт, медиа и разбор —",
    "всё удалено автоматически. ничего не осталось.",
  ],
  "result.startOver": "начать заново",
  "result.startOverSub": "загрузите другой чат для нового разбора",
  "result.selfDestructIn": "этот разбор самоуничтожится через",
  "result.selfDestructSub":
    "затем транскрипт, медиа и разбор удаляются автоматически — или уничтожьте сейчас",
  "result.deepProv1":
    "модель попросила ближе рассмотреть {n} фото, затем перечитала с ним перед глазами.",
  "result.deepProvN":
    "модель попросила ближе рассмотреть {n} фото, затем перечитала с ними перед глазами.",
  "result.readByRoute":
    "разобрано моделью {model} · через маршрут {route} — передан только текстовый транскрипт.",
  "result.readByNoRoute":
    "разобрано моделью {model} — передан только текстовый транскрипт.",
  "result.theModel": "модель",
  "result.provoke":
    "вот как передовая ии-модель составила ваш профиль — во благо или во вред.",
  "result.viewText": "посмотреть точный отправленный текст →",
  "result.heldNow": "сейчас хранится:",
  "result.heldRawMedia": "исходные медиа",
  "result.heldTranscript": "транскрипт",
  "result.heldRead": "разбор",
  "result.heldNone": "—",
  "result.nukeBtn": "уничтожить все мои данные",
  "result.nukeSub": "удаляет все ваши данные",
  "result.nukeSteps": [
    "> nuke --all",
    "удаляю исходные медиа .... готово",
    "удаляю транскрипт ........ готово",
    "удаляю разбор ............ готово",
    "удаляю эту сессию ........ готово",
    "",
    "Ваши данные удалены. начинаю заново…",
  ],

  // DataFlowModal — the custody-animation explainer
  "df.head": "как обрабатываются ваши данные",
  "df.h1": "Вот как мы обрабатываем ваши данные",
  "df.laneMessages": "сообщения →",
  "df.laneTranscript": "← транскрипт",
  "df.you": "ВЫ",
  "df.youSub": "Это ваше устройство",
  "df.serverHosted": "НАШ СЕРВЕР",
  "df.serverLocal": "ВАША МАШИНА",
  "df.serverSubHosted": "наш сайт",
  "df.serverSubLocal": "локально",
  "df.orVps": "ВАШ VPS",
  "df.llm": "llm",
  "df.noRetentionSub": " · без хранения",
  "df.trashLabel1": "изображения + сообщения",
  "df.trashLabel2": "уничтожены",
  "df.readObj": "разбор",
  "df.clab": "разбор изображений · остаётся {where}",
  "df.whereClab.server": "на нашем сервере",
  "df.whereClab.local": "на вашей машине",
  "df.where.server": "на сервере",
  "df.where.local": "на этой машине",
  "df.yourVps": "ваш vps",
  "df.theModel": "модель",
  "df.noRetention": " — без хранения",
  "df.caps": [
    "мультяшный кот закрывает лицо лапами",
    "двое людей за столом",
    "скриншот приложения",
    "размытая улица ночью",
    "тарелка еды сверху",
    "собака в прыжке на траве",
  ],
  "df.foot.you": "это вы, с вашим экспортированным чатом.",
  "df.foot.onlineHosted": "наш сервер выходит на связь.",
  "df.foot.onlineLocal": "ваша машина делает работу.",
  "df.foot.upload": "вы загружаете — .zip читается {where}.",
  "df.foot.decode": "изображения декодируются {where}. ",
  "df.foot.send": "только текстовый транскрипт уходит в {model} через {provider}{ret}.",
  "df.foot.analysis": "анализ завершён.",
  "df.foot.comeback": "разбор возвращается к вам.",
  "df.foot.destroy": "исходные изображения и сообщения уничтожаются — ничего не остаётся.",

  // chat bubble + context drawer
  "bubble.blindCaption": "— слепая подпись",
  "bubble.openInChat": "открыть в чате",
  "drawer.title": "ваш чат",
  "drawer.close": "закрыть",
  "drawer.loading": "загружаю чат…",
  "drawer.deleted": "этот чат больше недоступен — он был удалён.",
  "drawer.earlier": "↑ более ранние сообщения",
  "drawer.later": "↓ более поздние сообщения",
};

const it: Dict = {
  "common.back": "[ ← indietro ]",

  "landing.hero": "Carica la tua chat, per favore.",
  "landing.blurb":
    "Questa dimostrazione applica una politica di zero conservazione dei dati. Elaboriamo i media in locale e usiamo provider LLM open source per analizzare le conversazioni. Al termine della dimostrazione puoi eliminare i tuoi dati, altrimenti verranno cancellati automaticamente entro 24 ore al massimo. Buona dimostrazione",
  "landing.begin": "[ inizia → ]",
  "landing.quotaLeft": "{remaining} di {limit} letture rimaste oggi",
  "landing.quotaNone":
    "hai usato tutte le {limit} letture di oggi — si azzerano entro un giorno",
  "landing.source": "Sorgente",
  "landing.dataCycle": "Ciclo dei dati",

  "start.step1": "passo 1/4 · piattaforma",
  "start.step2": "passo 2/4 · esportazione",
  "start.selectPlatform": "seleziona la piattaforma",
  "start.exportFrom": "esporta da {platform}",
  "start.thenDrop": "…poi trascina lo .zip qui sotto.",
  "start.chooseZip": "scegli .zip",
  "start.uploading": "caricamento…",
  "start.uploadBtn": "carica .zip →",
  "start.consent":
    "questa è una mia conversazione e non contiene contenuti illegali.",
  "start.errRate":
    "Hai raggiunto il limite di letture per ora. Riprova più tardi.",
  "start.errTooLarge": "Questo export della chat è troppo grande per caricarlo qui.",
  "start.errWrongPlatform":
    "sembra un export di {found}, ma è selezionato {selected} — torna indietro e scegli {found}.",
  "start.errNotExport":
    "non sembra un export di {platform} — nessun file di chat nello zip. carica lo .zip esattamente come creato da {platform}.",
  "start.bgPause": "il caricamento si mette in pausa quando la scheda è in background — tienila visibile fino alla fine.",
  "start.wa.iphone": [
    "apri la chat in whatsapp.",
    "tocca il nome del contatto / gruppo in alto.",
    "scorri in basso → «esporta chat».",
    "scegli «allega media» — whatsapp crea uno .zip.",
  ],
  "start.wa.android": [
    "apri la chat in whatsapp.",
    "tocca ⋮ (in alto a destra) → altro → «esporta chat».",
    "scegli «includi media» — whatsapp crea uno .zip.",
  ],
  "start.mobileHint":
    "sul telefono servono un paio di tocchi in più — è più comodo su un computer, ma funziona anche qui.",
  "start.handoff.ios":
    "nel menu di condivisione tocca «Salva su File», poi tocca «scegli .zip» qui sotto e seleziona quel file.",
  "start.handoff.android":
    "salva lo .zip in File o Download, poi tocca «scegli .zip» qui sotto e selezionalo.",
  "start.handoff.desktop":
    "esportato dal telefono? invia lo .zip a questo computer (airdrop, email, drive) — poi sceglilo qui sotto.",
  "start.tgMobile.title": "per telegram serve un computer",
  "start.tgMobile.body":
    "telegram esporta le chat solo da Telegram Desktop — non si può fare dal telefono. apri peeping-tom.com su un laptop o desktop, oppure scegli una chat whatsapp.",
  "start.tg": [
    "usa telegram desktop da desktop.telegram.org — solo questa versione fa l’esportazione JSON.",
    "apri la chat → ⋮ → «esporta cronologia chat».",
    "imposta il formato su «JSON leggibile dalla macchina» — NON html.",
    "spunta foto, messaggi vocali e video, e adesivi.",
    "comprimi in zip la cartella esportata, trascina lo .zip qui sotto.",
  ],

  "insp.step3upload": "passo 3/4 · caricamento",
  "start.mode.fast": "[ veloce · prima il testo ]",
  "start.mode.deep": "[ profonda · tutti i media ]",
  "start.mode.fastSub":
    "legge prima il testo, poi apre solo i media che ritiene necessari — la lettura più rapida.",
  "start.mode.deepSub":
    "decodifica ogni foto e vocale mentre legge, integrandoli come prove — la lettura più completa, più lenta.",
  "insp.requestsTitle": "cosa la lettura ha chiesto di aprire",
  "insp.requestingHero": "sceglie cosa aprire",
  "insp.decodingReqHero": "apro ciò che la lettura ha chiesto",
  "insp.foldingHero": "rivedo con le nuove prove",
  "insp.foldRound": "rivedo la lettura · giro {round}",
  "insp.bgDecode": "decodifica media in background  {done}/{total}",
  "slice.tooBig": "questo export pesa {size} — oltre il limite di caricamento di {cap}. ",
  "slice.tooLong": "questa conversazione è più lunga di quanto una lettura possa reggere in modo coerente. ",
  "slice.reading": "lo leggo su questo dispositivo per proporti un taglio — nulla viene caricato.",
  "slice.pick": "tieni una parte della conversazione — tagliala qui, in locale:",
  "slice.latest": "[ la parte più recente ]",
  "slice.earliest": "[ la più vecchia ]",
  "slice.middle": "[ il mezzo ]",
  "slice.window": "finestra",
  "slice.unitTokens": "token",
  "slice.selected": "{n} messaggi · {range}",
  "slice.fits": "ci sta ✓",
  "slice.over": "ancora troppo grande ✗",
  "slice.cut": "[ taglia e continua → ]",
  "slice.cancel": "[ scegli un altro file ]",
  "slice.building": "impacchetto il taglio — {done}/{total} file…",
  "slice.failed": "impossibile leggere questo zip qui — prova un export più piccolo.",
  "slice.slicedName": "taglio:",
  "result.sliceProv":
    "questa lettura copre {range} — un taglio fatto in locale da un export più grande; nulla al di fuori è stato caricato.",
  "insp.step3parse": "passo 3/4 · analisi",
  "insp.step4": "passo 4/4 · la lettura",
  "insp.errorStep": "errore",
  "insp.errorHero": "qualcosa si è rotto",
  "insp.configStep": "config",
  "insp.configHero": "nessuna rotta di lettura",
  "insp.configHint":
    "imposta una rotta di lettura (o ROUTE_A_PROVIDER=mock) e riprova.",
  "insp.readingFallback": "il modello sta leggendo la trascrizione…",
  "insp.readingChat": "sto leggendo la tua chat",
  "insp.justDecoded": "appena decodificato",
  "insp.thinking": "sto pensando…",
  "insp.composing": "sto componendo la tua lettura…",
  "insp.uploadingHero": "sto caricando la tua chat",
  "insp.parsingHero": "sto analizzando la tua chat",
  "insp.uploadingBody1": "invio chat.zip a questa macchina…",
  "insp.uploadingBody2": "il file originale resta in locale",
  "insp.custodyLocal": "elaborato su questa macchina — nulla l’ha lasciata",
  "insp.parsingFallback": "sto analizzando la tua chat…",
  "insp.parsingBody":
    "leggo i messaggi e trascrivo i vocali — in locale",
  "insp.phaseReinspect": "ricontrollo",
  "insp.parsingShort": "analisi…",
  "insp.tips": [
    "il modello legge ciò che è implicito",
    "gli schemi emergono nel tempo",
    "una storia lunga può richiedere qualche minuto di lettura.",
  ],
  "insp.etaLeft": "~{eta} rimasti",

  "result.loadingHero": "sto caricando la lettura",
  "result.hero": "la lettura",
  "result.destroyedHero": "autodistrutta",
  "result.destroyedBody": [
    "questa lettura si è autodistrutta.",
    "",
    "la trascrizione, i media e la lettura —",
    "tutto eliminato automaticamente. non resta nulla.",
  ],
  "result.startOver": "ricomincia",
  "result.startOverSub": "carica un’altra chat per una nuova lettura",
  "result.selfDestructIn": "questa lettura si autodistrugge tra",
  "result.selfDestructSub":
    "poi trascrizione, media e lettura vengono eliminati automaticamente — o distruggili ora",
  "result.deepProv1":
    "il modello ha chiesto di osservare più da vicino {n} foto, poi ha riletto tenendola in vista.",
  "result.deepProvN":
    "il modello ha chiesto di osservare più da vicino {n} foto, poi ha riletto tenendole in vista.",
  "result.readByRoute":
    "letto da {model} · tramite la rotta {route} — è passata solo la trascrizione testuale.",
  "result.readByNoRoute":
    "letto da {model} — è passata solo la trascrizione testuale.",
  "result.theModel": "il modello",
  "result.provoke":
    "ecco come un modello di ia di frontiera ti ha profilato — nel bene o nel male.",
  "result.viewText": "vedi il testo esatto che è stato inviato →",
  "result.heldNow": "conservato ora:",
  "result.heldRawMedia": "media originali",
  "result.heldTranscript": "trascrizione",
  "result.heldRead": "la lettura",
  "result.heldNone": "—",
  "result.nukeBtn": "distruggi tutti i miei dati",
  "result.nukeSub": "elimina tutti i tuoi dati",
  "result.nukeSteps": [
    "> nuke --all",
    "elimino i media originali . fatto",
    "elimino la trascrizione .. fatto",
    "elimino la lettura ....... fatto",
    "elimino questa sessione .. fatto",
    "",
    "I tuoi dati sono eliminati. ricomincio…",
  ],

  // DataFlowModal — the custody-animation explainer
  "df.head": "come vengono trattati i tuoi dati",
  "df.h1": "Ecco come trattiamo i tuoi dati",
  "df.laneMessages": "messaggi →",
  "df.laneTranscript": "← trascrizione",
  "df.you": "TU",
  "df.youSub": "Questo è il tuo dispositivo",
  "df.serverHosted": "IL NOSTRO SERVER",
  "df.serverLocal": "LA TUA MACCHINA",
  "df.serverSubHosted": "il nostro sito",
  "df.serverSubLocal": "locale",
  "df.orVps": "IL TUO VPS",
  "df.llm": "llm",
  "df.noRetentionSub": " · nessuna conservazione",
  "df.trashLabel1": "immagini + messaggi",
  "df.trashLabel2": "distrutti",
  "df.readObj": "lettura",
  "df.clab": "analisi delle immagini · resta {where}",
  "df.whereClab.server": "sul nostro server",
  "df.whereClab.local": "sulla tua macchina",
  "df.where.server": "sul server",
  "df.where.local": "su questa macchina",
  "df.yourVps": "il tuo vps",
  "df.theModel": "il modello",
  "df.noRetention": " — nessuna conservazione",
  "df.caps": [
    "un gatto dei cartoni che si copre la faccia",
    "due persone a un tavolo",
    "uno screenshot di un’app",
    "una strada sfocata di notte",
    "un piatto di cibo, dall’alto",
    "un cane a mezz’aria sull’erba",
  ],
  "df.foot.you": "questo sei tu, con la tua chat esportata.",
  "df.foot.onlineHosted": "il nostro server si accende.",
  "df.foot.onlineLocal": "la tua macchina fa il lavoro.",
  "df.foot.upload": "carichi — lo .zip viene letto {where}.",
  "df.foot.decode": "le immagini vengono decodificate {where}. ",
  "df.foot.send": "solo la trascrizione testuale va a {model} tramite {provider}{ret}.",
  "df.foot.analysis": "analisi completata.",
  "df.foot.comeback": "la lettura torna a te.",
  "df.foot.destroy": "le immagini e i messaggi originali vengono distrutti — non resta nulla.",

  // chat bubble + context drawer
  "bubble.blindCaption": "— didascalia alla cieca",
  "bubble.openInChat": "apri nella chat",
  "drawer.title": "la tua chat",
  "drawer.close": "chiudi",
  "drawer.loading": "sto caricando la chat…",
  "drawer.deleted": "questa chat non è più disponibile — è stata eliminata.",
  "drawer.earlier": "↑ messaggi precedenti",
  "drawer.later": "↓ messaggi successivi",
};

const DICT: Record<Lang, Dict> = { en, ru, it };

function detect(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && (LANGS as string[]).includes(saved)) return saved as Lang;
  } catch {
    /* localStorage may be unavailable (SSR / privacy mode) */
  }
  const nav =
    (typeof navigator !== "undefined" &&
      (navigator.language || (navigator.languages || [])[0])) ||
    "en";
  const code = nav.toLowerCase().split("-")[0];
  return (LANGS as string[]).includes(code) ? (code as Lang) : "en";
}

function interp(s: string, vars?: Record<string, string | number>): string {
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (_, k) =>
    k in vars ? String(vars[k]) : `{${k}}`
  );
}

interface Ctx {
  lang: Lang;
  setLang: (l: Lang) => void;
}
const I18nCtx = createContext<Ctx>({ lang: "en", setLang: () => undefined });

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(detect);
  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    try {
      localStorage.setItem(STORAGE_KEY, l);
    } catch {
      /* ignore */
    }
  }, []);
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);
  const value = useMemo(() => ({ lang, setLang }), [lang, setLang]);
  return <I18nCtx.Provider value={value}>{children}</I18nCtx.Provider>;
}

export function useLang() {
  const { lang, setLang } = useContext(I18nCtx);
  return [lang, setLang] as const;
}

// t() for scalar strings, tList() for the array entries (steps, tips, receipt).
// Both interpolate {vars}; both fall back to English if a key is somehow missing.
export function useT() {
  const { lang } = useContext(I18nCtx);
  const t = useCallback(
    (key: MsgKey, vars?: Record<string, string | number>) => {
      const v = DICT[lang][key] ?? en[key];
      return interp(Array.isArray(v) ? v.join("\n") : v, vars);
    },
    [lang]
  );
  const tList = useCallback(
    (key: MsgKey, vars?: Record<string, string | number>) => {
      const v = DICT[lang][key] ?? en[key];
      return (Array.isArray(v) ? v : [v]).map((s) => interp(s, vars));
    },
    [lang]
  );
  return { t, tList, lang };
}
