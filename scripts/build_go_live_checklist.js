const { Document, Packer, Paragraph, TextRun,
        Header, Footer, AlignmentType, LevelFormat, HeadingLevel,
        BorderStyle, ShadingType, PageNumber, PageBreak } = require('docx');
const fs = require('fs');

const FONT = "Arial";
const ORANGE = "E86854";
const GREEN = "2E7D32";
const RED = "C62828";
const GRAY = "888888";
const LIGHT_BG = "FFF4E6";
const SUCCESS_BG = "E8F5E9";
const DANGER_BG = "FFEBEE";

const P = (text, opts = {}) => new Paragraph({
  spacing: { after: 120 },
  ...opts,
  children: Array.isArray(text)
    ? text.map(t => t instanceof TextRun ? t : new TextRun({ ...t, font: FONT }))
    : [new TextRun({ text, font: FONT })],
});

const H1 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_1,
  spacing: { before: 360, after: 200 },
  children: [new TextRun({ text, bold: true, size: 32, font: FONT, color: ORANGE })],
});

const H2 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_2,
  spacing: { before: 280, after: 140 },
  children: [new TextRun({ text, bold: true, size: 26, font: FONT })],
});

const Bullet = (parts, level = 0) => new Paragraph({
  numbering: { reference: "bullets", level },
  spacing: { after: 60 },
  children: parts.map(r => new TextRun({ ...r, font: FONT })),
});

const Check = (text) => new Paragraph({
  spacing: { after: 80 },
  children: [
    new TextRun({ text: "[ ]  ", bold: true, size: 22, font: FONT }),
    new TextRun({ text, size: 22, font: FONT }),
  ],
});

const Note = (parts, color = ORANGE, bg = LIGHT_BG) => new Paragraph({
  spacing: { before: 100, after: 100 },
  shading: { fill: bg, type: ShadingType.CLEAR },
  border: { left: { style: BorderStyle.SINGLE, size: 12, color, space: 8 } },
  indent: { left: 200 },
  children: parts.map(r => new TextRun({ ...r, font: FONT })),
});

const Warning = (parts) => Note(
  [{ text: "[!] ", bold: true }, ...parts],
  RED, DANGER_BG,
);

const Success = (parts) => Note(
  [{ text: "[OK] ", bold: true, color: GREEN }, ...parts],
  GREEN, SUCCESS_BG,
);

const HR = () => new Paragraph({
  spacing: { before: 200, after: 200 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: ORANGE, space: 1 } },
  children: [new TextRun("")],
});

const children = [
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 1400, after: 200 },
    children: [new TextRun({ text: "Запуск social_inbox", bold: true, size: 56, font: FONT, color: ORANGE })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 400 },
    children: [new TextRun({ text: "Чек-лист и инструкция для Юлии", size: 28, font: FONT })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
    children: [new TextRun({ text: "Как мы будем запускать автоматизацию Instagram-воронки", italics: true, size: 22, font: FONT, color: "555555" })],
  }),
  new Paragraph({ children: [new PageBreak()] }),

  H1("Что мы запускаем"),
  P("После нескольких недель разработки у нас готова система автоматизации Instagram-воронки. Что она умеет:"),
  Bullet([{ text: "Подписчик пишет «ОЧИЩЕНИЕ» в комментариях под твоим Reels - автоматически получает DM с приглашением в Telegram" }]),
  Bullet([{ text: "Подписчик пишет тебе в DM первый раз - получает приветственное сообщение с переходом в Telegram" }]),
  Bullet([{ text: "Подписчик задаёт вопросы в DM - AI (Claude) отвечает дружелюбно и кратко" }]),
  Bullet([{ text: "Если вопрос про симптомы / болезнь / беременность - AI сразу передаёт тебе" }]),
  Bullet([{ text: "Если подписчик пишет «оператор» - передаёт тебе" }]),
  Bullet([{ text: "Ты видишь все handover-диалоги в админке и отвечаешь лично" }]),
  Bullet([{ text: "Каждое утро получаешь сводку вчерашних результатов" }]),

  Note([
    { text: "Главный показатель успеха: ", bold: true },
    { text: "сколько подписчиков из Instagram реально дошли до твоего Telegram-бота и прошли квиз. Эту метрику видно в админке." },
  ]),

  HR(),

  H1("План запуска"),
  P("Запускаем не сразу всё, а постепенно (canary rollout). Это снижает риски: если что-то идёт не так, мы видим это на одном Reels, а не на всём контенте сразу."),

  H2("Подготовка (за 2-3 дня до запуска)"),
  Check("Все credentials и доступы переданы Виктору (SendPulse, домен, VPS)"),
  Check("Виктор подтвердил, что smoke tests прошли"),
  Check("Notification bot работает - ты получила тестовое сообщение"),
  Check("Daily digest пришёл утром - ты видишь сводку"),
  Check("Ты потренировалась заходить в админку (https://inbox-admin.<domain>)"),
  Check("Ты потренировалась отвечать на handover в админке"),
  Check("Бэкапы работают - Виктор показал, что pg_dump запускается раз в день"),

  H2("День запуска"),
  P("Это сам день, когда первый Reels пойдёт с автоматизацией."),

  Check("Утро: проверь, что админка открывается"),
  Check("Утро: попроси Виктора запустить финальную smoke-проверку"),
  Check("В админке проверь: keyword «очищение» активен (Ключевые слова - найди в списке)"),
  Check("В админке проверь: scenario «default_purify_comment» активен (Сценарии - найди в списке)"),
  Check("Опубликуй ОДИН Reels с CTA «Напиши ОЧИЩЕНИЕ в комментариях»"),

  Warning([
    { text: "Только один Reels на день запуска. ", bold: true },
    { text: "Не публикуй сразу 5 видео с разными ключевыми словами. Сначала наблюдаем 24 часа, потом расширяем." },
  ]),

  Check("Попроси кого-то (Виктора, подругу с другого аккаунта) написать «ОЧИЩЕНИЕ» в комментариях под Reels"),
  Check("Подожди 1-2 минуты"),
  Check("Этот человек должен получить DM с приглашением в Telegram"),
  Check("Кликни на «Перейти в Telegram» в этом DM - должно открыться @yuliya_purify_bot"),
  Check("В bot_purify должно быть персонализированное приветствие («Привет, ...! Здорово, что заинтересовалась программой Очищение...»)"),

  Success([
    { text: "Если все шаги прошли успешно - система работает. ", bold: true },
    { text: "Можно идти дальше." },
  ]),

  HR(),

  H2("Первые 24 часа"),
  P("Главная задача - наблюдать. Не паниковать, не делать резких движений."),

  Bullet([{ text: "Раз в 2-3 часа открывай админку - раздел «Входящие»" }]),
  Bullet([{ text: "Если есть handover-диалоги - отвечай в админке через форму «Ответ от Юли»" }]),
  Bullet([{ text: "Получаешь в Telegram уведомление о handover? Зайди в админку и ответь оттуда" }]),
  Bullet([{ text: "Если что-то странное - скрин и Виктору" }]),

  P("Что в принципе НЕ должно происходить (если случается - Виктору):"),
  Bullet([{ text: "Подписчики получают DM с медицинскими утверждениями («вылечит», «гарантирую»)" }]),
  Bullet([{ text: "Бот отвечает по 5 раз на одно сообщение" }]),
  Bullet([{ text: "DM приходит спустя 10+ минут (норма - 30 секунд)" }]),
  Bullet([{ text: "Юля не получает уведомления о handover" }]),

  HR(),

  H2("День 2-7: расширение"),
  P("Если за 24 часа всё работало стабильно, плавно расширяем:"),

  Check("День 2: добавь keyword под второй Reels (например, «МАСЛА» если делаешь о маслах)"),
  Check("День 3-4: следи за статистикой в админке - сколько лидов, сколько conversion"),
  Check("День 5-7: если всё хорошо, можно использовать на постоянной основе"),

  Note([
    { text: "Как добавить новый keyword: ", bold: true },
    { text: "Админка - Ключевые слова - Добавить keyword - введи слово, тип «contains», где «comment», сценарий «default_purify_comment» или другой." },
  ]),

  HR(),

  H1("Если что-то пошло не так"),

  H2("Уровень 1: Странный диалог"),
  P("Сценарий: один подписчик получает странный ответ от AI."),
  Bullet([{ text: "Открой этот диалог в админке" }]),
  Bullet([{ text: "Переключатель «AI режим включён» - выключи" }]),
  Bullet([{ text: "Этот человек больше не получит ответов от AI; ты можешь ответить лично через форму" }]),

  H2("Уровень 2: Жалоба или спам"),
  P("Сценарий: один человек жалуется на бота / много спам-комментариев / странные DM."),
  Bullet([{ text: "Зайди в админку - Ключевые слова" }]),
  Bullet([{ text: "Деактивируй (выключи галочку «Активен») у проблемного keyword" }]),
  Bullet([{ text: "Acquisition остановлен, существующие диалоги продолжаются как раньше" }]),
  Bullet([{ text: "Напиши Виктору - разберёмся в причине" }]),

  H2("Уровень 3: Что-то совсем не работает"),
  P("Сценарий: бот вообще не отвечает / ошибки в админке / Виктор недоступен."),
  Bullet([{ text: "Зайди в SendPulse - Чат-боты" }]),
  Bullet([{ text: "Отключи webhook (одна кнопка)" }]),
  Bullet([{ text: "Подписчики, написавшие в DM, не получат ответа - это нормально для emergency" }]),
  Bullet([{ text: "Telegram-бот @yuliya_purify_bot продолжает работать отдельно - это другой сервис" }]),

  Warning([
    { text: "В emergency-ситуации лучше отключиться, чем продолжать работу с проблемами. ", bold: true },
    { text: "Подписчик, не получивший ответа, гораздо лучше чем подписчик, получивший неправильный или потенциально опасный ответ." },
  ]),

  HR(),

  H1("Контакты для emergency"),

  P([{ text: "Виктор: ", bold: true }, { text: "Telegram, прямой звонок, WhatsApp" }]),
  P([{ text: "SendPulse поддержка: ", bold: true }, { text: "support@sendpulse.com" }]),
  P([{ text: "Anthropic поддержка (если AI вообще не отвечает): ", bold: true }, { text: "support@anthropic.com" }]),

  HR(),

  H1("После запуска"),
  P("Через неделю стабильной работы возвращаемся к обычной жизни:"),

  Bullet([{ text: "Раз в день открой админку утром, проверь handover-диалоги" }]),
  Bullet([{ text: "Раз в день читай daily digest в Telegram" }]),
  Bullet([{ text: "Под новые Reels добавляй keyword за 30 секунд через админку" }]),
  Bullet([{ text: "Раз в неделю смотри статистику - растёт ли conversion" }]),

  Success([
    { text: "Молодец, что дошла досюда. ", bold: true },
    { text: "Эта система должна экономить тебе несколько часов в неделю и масштабировать аудиторию без проседания качества общения. Если ощущаешь, что что-то можно улучшить - скажи, мы доработаем." },
  ]),
];

const doc = new Document({
  creator: "Claude",
  title: "Go-Live Checklist",
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: FONT, color: ORANGE },
        paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: FONT },
        paragraph: { spacing: { before: 280, after: 140 }, outlineLevel: 1 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets",
        levels: [
          { level: 0, format: LevelFormat.BULLET, text: "*", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
        ] },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          children: [new TextRun({ text: "social_inbox - go-live", italics: true, color: GRAY, size: 18, font: FONT })],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [
            new TextRun({ children: ["Стр. ", PageNumber.CURRENT, " из ", PageNumber.TOTAL_PAGES], size: 18, font: FONT, color: GRAY }),
          ],
        })],
      }),
    },
    children,
  }],
});

Packer.toBuffer(doc).then(buf => {
  const out = process.argv[2] || "docs/Go_Live_Checklist.docx";
  fs.writeFileSync(out, buf);
  console.log("OK:", out, "size:", buf.length);
});
