<?php
/*
 * RTTY Station - enquête / survey (NL + EN)
 * Upload naar: https://www.pd3rah.nl/rttystation/enquete.php
 * Antwoorden worden per e-mail verstuurd; er wordt niets opgeslagen op de server.
 * Copyright (c) 2026 Richard A. Hendriksen (PD3RAH) - MIT License
 */

// ------------------------------------------------------------------ instellingen
$MAIL_TO   = 'info@pd3rah.nl';
$MAIL_FROM = 'noreply@pd3rah.nl';     // afzender op eigen domein (SPF) - mag ook info@pd3rah.nl zijn
$MIN_SECONDS = 4;                     // formulier sneller ingevuld = waarschijnlijk een bot
$WAIT_SECONDS = 60;                   // minimale tijd tussen twee inzendingen per bezoeker

session_start();
header('Content-Type: text/html; charset=utf-8');

$lang = (isset($_REQUEST['lang']) && $_REQUEST['lang'] === 'en') ? 'en' : 'nl';
$ver  = isset($_REQUEST['v']) ? substr(preg_replace('/[^0-9A-Za-z.\-]/', '', $_REQUEST['v']), 0, 20) : '';

function t($nl, $en) { global $lang; return $lang === 'nl' ? $nl : $en; }
function h($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function field($k, $max = 2000) {
    $v = isset($_POST[$k]) ? trim((string)$_POST[$k]) : '';
    $v = str_replace(["\r\n", "\r"], "\n", $v);
    return mb_substr($v, 0, $max, 'UTF-8');
}

$FEATURES = [
    'decoder' => ['RTTY-decoder', 'RTTY decoder'],
    'skimmer' => ['Skimmer (meerdere signalen)', 'Skimmer (multiple signals)'],
    'afsk'    => ['Zenden met AFSK (LSB-D)', 'Transmitting with AFSK (LSB-D)'],
    'fsk'     => ['Zenden met FSK (RTTY-mode, DTR/RTS)', 'Transmitting with FSK (RTTY mode, DTR/RTS)'],
    'macros'  => ['Macro\'s / live typen', 'Macros / live typing'],
    'tuning'  => ['Afstemindicator / klik om af te stemmen', 'Tuning indicator / click to tune'],
    'scope'   => ['Spectrumscope / waterval', 'Spectrum scope / waterfall'],
    'radio'   => ['Radiobediening (knop, banden, filters)', 'Radio control (knob, bands, filters)'],
    'log'     => ['Logboek / ADIF', 'Logbook / ADIF'],
];

$errors = [];
$sent = false;

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $honeypot = field('website', 200);
    $started  = (int)($_POST['ts'] ?? 0);
    $token    = $_POST['token'] ?? '';

    if ($honeypot !== '' || !hash_equals($_SESSION['token'] ?? '', $token) || time() - $started < $MIN_SECONDS) {
        $errors[] = t('Het formulier kon niet worden verwerkt. Probeer het opnieuw.',
                      'The form could not be processed. Please try again.');
    } elseif (isset($_SESSION['last_sent']) && time() - $_SESSION['last_sent'] < $WAIT_SECONDS) {
        $errors[] = t('Je hebt net al een enquête verstuurd. Wacht even en probeer het opnieuw.',
                      'You just sent a survey. Please wait a moment and try again.');
    }

    $overall = (int)field('overall', 1);
    $decode  = (int)field('decode', 1);
    if ($overall < 1 || $overall > 5) {
        $errors[] = t('Geef een algemeen oordeel (1 tot 5 sterren).', 'Please give an overall rating (1 to 5 stars).');
    }
    $email = field('email', 200);
    if ($email !== '' && !filter_var($email, FILTER_VALIDATE_EMAIL)) {
        $errors[] = t('Het e-mailadres is niet geldig.', 'The e-mail address is not valid.');
    }

    if (!$errors) {
        $used = [];
        foreach ($FEATURES as $k => $lbl) {
            if (!empty($_POST['f_' . $k])) { $used[] = $lbl[1]; }
        }
        $call = strtoupper(field('call', 20));
        $rows = [
            'Programma / version' => 'RTTY Station ' . ($ver ?: '?'),
            'Taal / language'     => $lang,
            'Algemeen / overall'  => str_repeat('*', $overall) . " ($overall/5)",
            'Decoder'             => $decode ? str_repeat('*', $decode) . " ($decode/5)" : '-',
            'Functies / features' => $used ? implode(', ', $used) : '-',
            'Radio'               => field('radio', 100) ?: '-',
            'Windows'             => field('windows', 30) ?: '-',
            'Call'                => $call ?: '-',
            'E-mail'              => $email ?: '-',
        ];
        $body = "RTTY Station - " . t('nieuwe enquête', 'new survey') . "\n" . str_repeat('=', 40) . "\n\n";
        foreach ($rows as $k => $v) { $body .= str_pad($k . ':', 22) . $v . "\n"; }
        $body .= "\n--- " . t('Problemen / bugs', 'Problems / bugs') . " ---\n" . (field('problems') ?: '-') . "\n";
        $body .= "\n--- " . t('Wensen / ideeën', 'Wishes / ideas') . " ---\n" . (field('ideas') ?: '-') . "\n";
        $body .= "\n--- " . t('Overig', 'Other') . " ---\n" . (field('other') ?: '-') . "\n";
        $body .= "\n" . str_repeat('-', 40) . "\n" . gmdate('Y-m-d H:i') . " UTC\n";

        $subject = 'RTTY Station enquete' . ($call ? " - $call" : '') . " ($overall/5)";
        $headers = [
            'From: RTTY Station <' . $MAIL_FROM . '>',
            'MIME-Version: 1.0',
            'Content-Type: text/plain; charset=UTF-8',
            'Content-Transfer-Encoding: 8bit',
            'X-Mailer: RTTYStation-survey',
        ];
        if ($email !== '') { $headers[] = 'Reply-To: ' . $email; }
        $ok = mail($MAIL_TO, '=?UTF-8?B?' . base64_encode($subject) . '?=', $body, implode("\r\n", $headers),
                   '-f' . $MAIL_FROM);
        if ($ok) {
            $sent = true;
            $_SESSION['last_sent'] = time();
        } else {
            $errors[] = t('Versturen is mislukt. Probeer het later nog eens of mail naar ',
                          'Sending failed. Please try again later or e-mail ') . $MAIL_TO . '.';
        }
    }
}

$_SESSION['token'] = bin2hex(random_bytes(16));
$other_lang = $lang === 'nl' ? 'en' : 'nl';
?>
<!DOCTYPE html>
<html lang="<?= $lang ?>">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title><?= t('RTTY Station – enquête', 'RTTY Station – survey') ?></title>
<style>
  :root { --bg:#07121b; --panel:#0c1d2b; --panel2:#10283b; --border:#1a3d55; --acc:#ffb02e;
          --acc2:#5cc8ff; --text:#d8edf6; --dim:#6f93a8; --red:#ff4d5e; --green:#4be38a; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:16px/1.5 "Segoe UI", system-ui, sans-serif; }
  .wrap { max-width:760px; margin:0 auto; padding:28px 18px 60px; }
  header { display:flex; align-items:center; gap:14px; margin-bottom:22px; }
  .logo { width:52px; height:52px; border-radius:50%; border:2px solid var(--acc); display:grid;
          place-items:center; color:var(--acc); font-weight:700; background:var(--panel); flex:none; font-size:13px; }
  h1 { margin:0; font-size:26px; }
  .sub { color:var(--dim); font-size:14px; }
  .lang { margin-left:auto; color:var(--acc2); text-decoration:none; font-size:14px;
          border:1px solid var(--border); padding:6px 12px; border-radius:8px; }
  .card { background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:18px 20px;
          margin-bottom:14px; }
  .card h2 { margin:0 0 10px; font-size:14px; letter-spacing:.04em; color:var(--dim);
             border-left:3px solid var(--acc); padding-left:8px; text-transform:uppercase; }
  label.q { display:block; font-weight:600; margin:6px 0; }
  .stars { display:flex; flex-direction:row-reverse; justify-content:flex-end; gap:4px; }
  .stars input { display:none; }
  .stars label { font-size:32px; color:#2a4a62; cursor:pointer; line-height:1; }
  .stars input:checked ~ label, .stars label:hover, .stars label:hover ~ label { color:var(--acc); }
  .checks { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:6px 14px; }
  .checks label { display:flex; gap:8px; align-items:center; }
  input[type=text], input[type=email], select, textarea {
          width:100%; background:var(--panel2); color:var(--text); border:1px solid var(--border);
          border-radius:8px; padding:9px 11px; font:inherit; }
  input:focus, select:focus, textarea:focus { outline:none; border-color:var(--acc); }
  textarea { min-height:90px; resize:vertical; }
  .row { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  @media (max-width:560px) { .row { grid-template-columns:1fr; } }
  .hp { position:absolute; left:-5000px; }
  button { background:var(--acc); color:#03131b; border:0; border-radius:10px; padding:12px 26px;
           font:600 16px "Segoe UI", system-ui, sans-serif; cursor:pointer; }
  button:hover { filter:brightness(1.08); }
  .msg { padding:14px 18px; border-radius:10px; margin-bottom:14px; }
  .err { background:#3a1218; border:1px solid var(--red); }
  .ok  { background:#0f2f22; border:1px solid var(--green); }
  .note { color:var(--dim); font-size:13px; }
  a { color:var(--acc2); }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">RTTY</div>
    <div>
      <h1><?= t('RTTY Station – enquête', 'RTTY Station – survey') ?></h1>
      <div class="sub"><?= t('voor de Icom IC-7300', 'for the Icom IC-7300') ?><?= $ver ? ' · versie ' . h($ver) : '' ?></div>
    </div>
    <a class="lang" href="?lang=<?= $other_lang ?><?= $ver ? '&v=' . h($ver) : '' ?>"><?= $lang === 'nl' ? 'English' : 'Nederlands' ?></a>
  </header>

<?php if ($sent): ?>
  <div class="msg ok">
    <strong><?= t('Bedankt voor je reactie!', 'Thank you for your feedback!') ?></strong><br>
    <?= t('Je antwoorden zijn verstuurd naar PD3RAH. Ze helpen om RTTY Station beter te maken. 73!',
          'Your answers have been sent to PD3RAH. They help to make RTTY Station better. 73!') ?>
  </div>
<?php else: ?>
  <?php foreach ($errors as $e): ?><div class="msg err"><?= h($e) ?></div><?php endforeach; ?>

  <p class="sub"><?= t('Het invullen duurt ongeveer 2 minuten. Alleen het algemene oordeel is verplicht.',
                       'This takes about 2 minutes. Only the overall rating is required.') ?></p>

  <form method="post" action="?lang=<?= $lang ?><?= $ver ? '&v=' . h($ver) : '' ?>">
    <input type="hidden" name="token" value="<?= h($_SESSION['token']) ?>">
    <input type="hidden" name="ts" value="<?= time() ?>">
    <div class="hp"><label>Website <input type="text" name="website" tabindex="-1" autocomplete="off"></label></div>

    <div class="card">
      <h2><?= t('Oordeel', 'Rating') ?></h2>
      <label class="q"><?= t('Hoe tevreden ben je over RTTY Station?', 'How satisfied are you with RTTY Station?') ?> *</label>
      <div class="stars">
        <?php for ($i = 5; $i >= 1; $i--): ?>
          <input type="radio" id="o<?= $i ?>" name="overall" value="<?= $i ?>" <?= (int)field('overall', 1) === $i ? 'checked' : '' ?>>
          <label for="o<?= $i ?>" title="<?= $i ?>">★</label>
        <?php endfor; ?>
      </div>
      <label class="q"><?= t('Hoe goed decodeert de RTTY-decoder?', 'How well does the RTTY decoder copy?') ?></label>
      <div class="stars">
        <?php for ($i = 5; $i >= 1; $i--): ?>
          <input type="radio" id="d<?= $i ?>" name="decode" value="<?= $i ?>" <?= (int)field('decode', 1) === $i ? 'checked' : '' ?>>
          <label for="d<?= $i ?>" title="<?= $i ?>">★</label>
        <?php endfor; ?>
      </div>
    </div>

    <div class="card">
      <h2><?= t('Gebruik', 'Usage') ?></h2>
      <label class="q"><?= t('Welke functies gebruik je?', 'Which features do you use?') ?></label>
      <div class="checks">
        <?php foreach ($FEATURES as $k => $lbl): ?>
          <label><input type="checkbox" name="f_<?= $k ?>" value="1" <?= !empty($_POST['f_' . $k]) ? 'checked' : '' ?>> <?= h(t($lbl[0], $lbl[1])) ?></label>
        <?php endforeach; ?>
      </div>
      <div class="row" style="margin-top:12px">
        <div>
          <label class="q" for="radio"><?= t('Radio (en firmwareversie)', 'Radio (and firmware version)') ?></label>
          <input type="text" id="radio" name="radio" maxlength="100" placeholder="IC-7300, firmware 1.42" value="<?= h(field('radio', 100)) ?>">
        </div>
        <div>
          <label class="q" for="windows">Windows</label>
          <select id="windows" name="windows">
            <?php foreach (['', 'Windows 11', 'Windows 10', t('Anders', 'Other')] as $w): ?>
              <option value="<?= h($w) ?>" <?= field('windows', 30) === $w ? 'selected' : '' ?>><?= $w === '' ? '–' : h($w) ?></option>
            <?php endforeach; ?>
          </select>
        </div>
      </div>
    </div>

    <div class="card">
      <h2><?= t('Bevindingen', 'Findings') ?></h2>
      <label class="q" for="problems"><?= t('Problemen of fouten die je tegenkwam', 'Problems or bugs you ran into') ?></label>
      <textarea id="problems" name="problems" maxlength="2000"><?= h(field('problems')) ?></textarea>
      <label class="q" for="ideas"><?= t('Wensen en ideeën', 'Wishes and ideas') ?></label>
      <textarea id="ideas" name="ideas" maxlength="2000"><?= h(field('ideas')) ?></textarea>
      <label class="q" for="other"><?= t('Overige opmerkingen', 'Other comments') ?></label>
      <textarea id="other" name="other" maxlength="2000"><?= h(field('other')) ?></textarea>
    </div>

    <div class="card">
      <h2><?= t('Contact (optioneel)', 'Contact (optional)') ?></h2>
      <div class="row">
        <div>
          <label class="q" for="call"><?= t('Call', 'Callsign') ?></label>
          <input type="text" id="call" name="call" maxlength="20" value="<?= h(field('call', 20)) ?>">
        </div>
        <div>
          <label class="q" for="email"><?= t('E-mail (als je een reactie wilt)', 'E-mail (if you want a reply)') ?></label>
          <input type="email" id="email" name="email" maxlength="200" value="<?= h(field('email', 200)) ?>">
        </div>
      </div>
      <p class="note"><?= t('Je antwoorden worden alleen per e-mail naar PD3RAH gestuurd en worden niet op de website opgeslagen. Ze worden uitsluitend gebruikt om RTTY Station te verbeteren.',
                            'Your answers are only e-mailed to PD3RAH and are not stored on the website. They are used solely to improve RTTY Station.') ?></p>
    </div>

    <button type="submit"><?= t('Versturen', 'Send') ?></button>
  </form>
<?php endif; ?>
  <p class="note" style="margin-top:28px">RTTY Station · © 2026 Richard A. Hendriksen (PD3RAH) ·
    <a href="https://www.pd3rah.nl">www.pd3rah.nl</a></p>
</div>
</body>
</html>
