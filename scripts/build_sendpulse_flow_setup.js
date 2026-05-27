const { Document, Packer, Paragraph, TextRun,
        Header, Footer, AlignmentType, LevelFormat, HeadingLevel,
        BorderStyle, ShadingType, PageNumber, PageBreak } = require('docx');
const fs = require('fs');

const FONT = "Arial";
const MONO = "Consolas";
const ORANGE = "E86854";
const GREEN = "2E7D32";
const RED = "C62828";
const BLUE = "1565C0";
const GRAY = "888888";
const LIGHT_BG = "FFF4E6";
const CODE_BG = "F4F4F4";
const SUCCESS_BG = "E8F5E9";
const DANGER_BG = "FFEBEE";
const INFO_BG = "E3F2FD";

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

const Step = (n, text) => new Paragraph({
  spacing: { before: 220, after: 80 },
  children: [
    new TextRun({ text: `Шаг ${n}. `, bold: true, size: 24, font: FONT, color: ORANGE }),
    new TextRun({ text, bold: true, size: 24, font: FONT }),
  ],
});

const Field = (label, value) => new Paragraph({
  spacing: { after: 60 },
  indent: { left: 360 },
  children: [
    new TextRun({ text: `${label}: `, bold: true, font: FONT, size: 22 }),
    new TextRun({ text: value, font: FONT, size: 22 }),
  ],
});

const Code = (text) => new Paragraph({
  spacing: { before: 60, after: 120 },
  shading: { fill: CODE_BG, type: ShadingType.CLEAR },
  indent: { left: 200 },
  children: [new TextRun({ text, font: MONO, size: 20 })],
});

const Note = (parts, color = ORANGE, bg = LIGHT_BG) => new Paragraph({
  spacing: { before: 100, after: 100 },
  shading: { fill: bg, type: ShadingType.CLEAR },
  border: { left: { style: BorderStyle.SINGLE, size: 12, color, space: 8 } },
  indent: { left: 200 },
  children: parts.map(r => new TextRun({ ...r, font: FONT })),
});

const Warning = (parts) => Note([{ text: "[!] ", bold: true }, ...parts], RED, DANGER_BG);
const Info = (parts) => Note([{ text: "[i] ", bold: true }, ...parts], BLUE, INFO_BG);
const Success = (parts) => Note([{ text: "[OK] ", bold: true, color: GREEN }, ...parts], GREEN, SUCCESS_BG);

const HR = () => new Paragraph({
  spacing: { before: 200, after: 200 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: ORANGE, space: 1 } },
  children: [new TextRun("")],
});

const children = [
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 1400, after: 200 },
    children: [new TextRun({ text: "SendPulse Flow Setup", bold: true, size: 52, font: FONT, color: ORANGE })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 400 },
    children: [new TextRun({ text: "Настройка comment-to-DM acquisition для Юли", size: 28, font: FONT })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
    children: [new TextRun({ text: "Как настроить автоответ в DM при ключевом слове в комментариях Reels", italics: true, size: 22, font: FONT, color: "555555" })],
  }),
  new Paragraph({ children: [new PageBreak()] }),

  H1("Зачем эта настройка"),
  P("В нашей системе есть две части acquisition (как лиды попадают к нам в воронку):"),
  Bullet([{ text: "DM-acquisition — подписчик пишет тебе в Direct первым. Это обрабатывает наш бэкенд: получает сообщение через polling, отвечает welcome'ом, дальше Claude общается дружелюбно." }]),
  Bullet([{ text: "Comment-to-DM acquisition — подписчик пишет «ОЧИЩЕНИЕ» (или другое keyword) в комментариях под твоим Reels. Это нужно настроить ВНУТРИ SendPulse Flow Builder — потому что SendPulse не отдаёт комментарии через REST API (даже на платном тарифе)." }]),

  Info([
    { text: "Хорошая новость: ", bold: true },
    { text: "после первого DM (который отправит сам SendPulse по флоу) — все последующие сообщения подхватывает наш бэкенд, и дальше включается обычная логика (welcome, AI-ответы, handover, переход в Telegram)." },
  ]),

  P("Документ описывает пошаговую настройку Trigger + Flow в SendPulse UI. После выполнения этих шагов acquisition через комментарии заработает без правок кода."),

  HR(),

  H1("Подготовка"),
  Bullet([{ text: "Доступ к https://login.sendpulse.com под аккаунтом, к которому привязан Instagram Юли" }]),
  Bullet([{ text: "Бот для Instagram уже создан и активен (status=Active)" }]),
  Bullet([{ text: "Известно имя Telegram-бота: @yuliya_purify_bot" }]),
  Bullet([{ text: "На руках есть тестовый аккаунт Instagram (со второго телефона/устройства) для проверки" }]),

  HR(),

  H1("Пошаговая настройка"),

  Step(1, "Открыть Instagram-бот в SendPulse"),
  Bullet([{ text: "Зайти на https://login.sendpulse.com" }]),
  Bullet([{ text: "В левом меню: Чат-боты (Chatbots)" }]),
  Bullet([{ text: "Перейти на вкладку «Instagram»" }]),
  Bullet([{ text: "Кликнуть на иконку Instagram-бота Юли" }]),

  Step(2, "Создать Trigger"),
  P("Trigger — это правило, которое запускает Flow. Нам нужен Trigger на keyword."),
  Bullet([{ text: "В меню бота: Триггеры (Triggers) → кнопка «Создать триггер» (New trigger)" }]),
  Bullet([{ text: "Тип триггера: Ключевое слово (Keyword)" }]),
  Field("Название (Name)", "Очищение acquisition"),
  Field("Ключевые слова (Keywords)", "ОЧИЩЕНИЕ, очищение, Очищение"),
  Field("Тип поиска (Keywords search type)", "Содержит (Contains)"),
  Field("Чувствительность к регистру", "Нет (Case-insensitive)"),

  Warning([
    { text: "Перечисляем ВСЕ варианты регистра ", bold: true },
    { text: "и оставляем 'Contains' — это страхует от того, что подписчик напишет 'хочу очищение!' или 'ОЧИЩЕНИЕ программу' и т.п. Не используй 'Equals' (равно) — иначе сработает только на точное совпадение." },
  ]),

  Bullet([{ text: "Статус: Активен (Active)" }]),
  Bullet([{ text: "Сохранить (Save)" }]),

  Step(3, "Создать Flow"),
  P("Flow — это сценарий, который будет запущен Trigger'ом. Мы делаем простой одно-сообщения Flow."),

  Bullet([{ text: "В меню бота: Чат-боты → Flows (Чат-боты → Цепочки)" }]),
  Bullet([{ text: "Кнопка «Создать новую цепочку» (New flow)" }]),
  Field("Название цепочки", "Очищение → DM с deep-link"),

  P("В редакторе flow:"),
  Bullet([{ text: "Первый элемент уже создан — это Message (по умолчанию)" }]),
  Bullet([{ text: "Кликнуть на Message → выбрать тип «Generic template» (Универсальный шаблон с кнопками)" }]),

  P("Параметры элемента:"),
  Field("Title (Заголовок)", "Привет! 🌿"),
  Field("Subtitle (Подзаголовок)", "Расскажу подробнее про программу «Очищение» в Telegram-боте — там удобнее, и сразу пройдёшь квиз 💚"),

  P("Добавить кнопку (Add button):"),
  Field("Тип кнопки (Button type)", "URL (Ссылка)"),
  Field("Название кнопки (Title)", "Перейти в Telegram"),
  Field("URL", "https://t.me/yuliya_purify_bot?start=ig_sp_purify"),

  Info([
    { text: "Что такое 'ig_sp_purify' в URL: ", bold: true },
    { text: "это deep-link payload, который bot_purify парсит при /start. 'ig' = из Instagram, 'sp' = из SendPulse-флоу (не из polling-DM), 'purify' = слаг сценария. По нему bot_purify запустит правильную приветственную ветку." },
  ]),

  Bullet([{ text: "Сохранить элемент (Save element)" }]),
  Bullet([{ text: "Сохранить флоу (Save flow)" }]),

  Step(4, "Привязать Trigger к Flow"),
  Bullet([{ text: "Открыть созданный Trigger (Триггеры → Очищение acquisition)" }]),
  Bullet([{ text: "В поле 'Flow' выбрать только что созданный 'Очищение → DM с deep-link'" }]),
  Bullet([{ text: "Сохранить" }]),

  Step(5, "Активация"),
  Bullet([{ text: "Trigger: проверить что статус 'Активен'" }]),
  Bullet([{ text: "Flow: проверить что статус 'Активен'" }]),
  Bullet([{ text: "Сам бот: проверить что статус 'Активен' (Чат-боты → Instagram-бот → Status)" }]),

  Step(6, "Тестирование"),
  P("Чтобы проверить, что всё работает:"),

  Bullet([{ text: "1. Открой Instagram приложение под аккаунтом Юли" }]),
  Bullet([{ text: "2. Опубликуй любой Reels (или возьми существующий) с текстом «Напиши ОЧИЩЕНИЕ в комментариях»" }]),
  Bullet([{ text: "3. Попроси кого-то (Виктора, подругу с другого аккаунта) написать «ОЧИЩЕНИЕ» в комментариях под этим Reels" }]),
  Bullet([{ text: "4. Подожди 30-60 секунд" }]),
  Bullet([{ text: "5. Тот человек должен получить от тебя DM с кнопкой 'Перейти в Telegram'" }]),
  Bullet([{ text: "6. После клика на кнопку → открывается @yuliya_purify_bot с приветственным сообщением" }]),
  Bullet([{ text: "7. Параллельно в наш бэкенд через polling прилетит уведомление о новом контакте → создастся social_users запись" }]),

  Success([
    { text: "Если все шесть шагов сработали — настройка завершена. ", bold: true },
    { text: "Acquisition через комментарии работает, дальше всё на автомате." },
  ]),

  HR(),

  H1("Что происходит после настройки"),
  P("Полная цепочка (от комментария до квиза в Telegram):"),

  Bullet([{ text: "Подписчик пишет «ОЧИЩЕНИЕ» в комментарии под твоим Reels" }]),
  Bullet([{ text: "SendPulse детектит keyword → запускает Flow «Очищение → DM с deep-link»" }]),
  Bullet([{ text: "SendPulse автоматически отправляет подписчику DM с кнопкой 'Перейти в Telegram'" }]),
  Bullet([{ text: "Подписчик жмёт кнопку → переходит в Telegram → попадает в @yuliya_purify_bot" }]),
  Bullet([{ text: "bot_purify читает payload 'ig_sp_purify' → отправляет персонализированное приветствие + запускает квиз" }]),
  Bullet([{ text: "Параллельно наш social_inbox polling-ом фиксирует контакт → дальше Claude отвечает на любые сообщения этого подписчика в DM" }]),

  HR(),

  H1("Расширение на другие keyword'ы"),
  P("Когда захочешь добавить keyword под другой контент (например, «МАСЛА» для Reels про масла):"),

  Bullet([{ text: "Повторить Шаги 2-4: создать новый Trigger «Масла acquisition» с keywords 'МАСЛА, масла, Масла'" }]),
  Bullet([{ text: "Создать новый Flow «Масла → DM» с другим текстом и тем же deep-link форматом, например ig_sp_oils" }]),
  Bullet([{ text: "В bot_purify слаг 'oils' уже зарегистрирован — приветствие будет про масла" }]),

  Warning([
    { text: "Не создавай слишком много keyword'ов одновременно. ", bold: true },
    { text: "Начни с одного («ОЧИЩЕНИЕ»). После недели стабильной работы добавляй следующий. Так проще ловить проблемы." },
  ]),

  HR(),

  H1("Если что-то пошло не так"),

  H2("DM не приходит"),
  Bullet([{ text: "Проверь, что Trigger в статусе 'Активен' (Триггеры → найти «Очищение acquisition»)" }]),
  Bullet([{ text: "Проверь, что Flow в статусе 'Активен'" }]),
  Bullet([{ text: "Проверь, что сам бот в статусе 'Активен' (главная страница бота)" }]),
  Bullet([{ text: "Проверь, что подписчик НЕ заблокировал бот (можно увидеть в Контактах → найти его username → статус)" }]),

  H2("Подписчик получает DM, но кнопка ведёт не туда"),
  Bullet([{ text: "Открыть Flow → элемент Generic template → кнопка → проверить URL" }]),
  Bullet([{ text: "URL должен быть РОВНО https://t.me/yuliya_purify_bot?start=ig_sp_purify (без пробелов, без других символов)" }]),
  Bullet([{ text: "Если URL правильный, но в Telegram открывается обычный чат без приветствия — это вопрос к bot_purify, отдай Виктору скрин" }]),

  H2("В социал-инбоксе не появляется новый контакт"),
  Bullet([{ text: "Это окей! SendPulse Flow отправляет DM сам, через свой движок — НЕ через наш polling" }]),
  Bullet([{ text: "Наш polling подхватит контакт только когда подписчик напишет ОТВЕТ на это сообщение (или новое сообщение в DM)" }]),
  Bullet([{ text: "До тех пор контакт виден только в SendPulse UI (Чаты → найти подписчика)" }]),

  HR(),

  H1("Контакты"),
  P([{ text: "Виктор: ", bold: true }, { text: "Telegram (если что-то не работает на стороне social_inbox)" }]),
  P([{ text: "SendPulse поддержка: ", bold: true }, { text: "support@sendpulse.com (если проблемы с Trigger/Flow в их UI)" }]),
  P([{ text: "Документация SendPulse: ", bold: true }, { text: "https://sendpulse.com/integrations/api/chatbot/instagram" }]),
];

const doc = new Document({
  creator: "Claude",
  title: "SendPulse Flow Setup",
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
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
          children: [new TextRun({ text: "social_inbox · SendPulse Flow Setup", italics: true, color: GRAY, size: 18, font: FONT })],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ children: ["Стр. ", PageNumber.CURRENT, " из ", PageNumber.TOTAL_PAGES], size: 18, font: FONT, color: GRAY })],
        })],
      }),
    },
    children,
  }],
});

Packer.toBuffer(doc).then(buf => {
  const out = process.argv[2] || "../docs/SendPulse_Flow_Setup.docx";
  fs.writeFileSync(out, buf);
  console.log("OK:", out, "size:", buf.length);
});
