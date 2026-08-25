<?php
declare(strict_types=1);
require dirname(__DIR__) . '/src/ProviderForwarder.php';

function respond(int $status, array $body): never {
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode($body, JSON_UNESCAPED_SLASHES);
    exit;
}
function envInt(string $name, int $default): int {
    $value = getenv($name);
    return $value === false ? $default : max(1, (int)$value);
}
function bearer(): string {
    $header = (string)($_SERVER['HTTP_AUTHORIZATION']
        ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION']
        ?? getenv('HTTP_AUTHORIZATION')
        ?: '');
    return str_starts_with($header, 'Bearer ') ? substr($header, 7) : '';
}
function validSession(string $id): bool {
    return preg_match('/^[A-Za-z0-9_.-]{16,128}$/D', $id) === 1;
}
function sessionDir(string $root, string $id): string {
    return $root . DIRECTORY_SEPARATOR . hash('sha256', $id);
}
function readJson(): array {
    $raw = file_get_contents('php://input');
    if ($raw === false || strlen($raw) > 65536) respond(400, ['error'=>'invalid_json']);
    $data = json_decode($raw, true);
    if (!is_array($data)) respond(400, ['error'=>'invalid_json']);
    return $data;
}

function requestPath(): string {
    $path = parse_url((string)($_SERVER['REQUEST_URI'] ?? '/'), PHP_URL_PATH) ?: '/';
    $configured = trim((string)(getenv('RELAY_BASE_PATH') ?: ''));
    if ($configured !== '') {
        $base = '/' . trim($configured, '/');
    } else {
        $scriptName = str_replace('\\', '/', (string)($_SERVER['SCRIPT_NAME'] ?? ''));
        $base = rtrim(str_replace('/index.php', '', $scriptName), '/');
    }
    if ($base !== '' && $base !== '/') {
        if ($path === $base) return '/';
        if (str_starts_with($path, $base . '/')) return substr($path, strlen($base));
    }
    return $path;
}

function streamElevenLabsTts(array $data): never {
    $text = trim((string)($data['text'] ?? ''));
    $length = function_exists('mb_strlen') ? mb_strlen($text, 'UTF-8') : strlen($text);
    if ($text === '' || $length > 5000) respond(400, ['error'=>'invalid_tts_text']);
    if (!function_exists('curl_init')) respond(503, ['error'=>'curl_unavailable']);

    $apiKey = trim((string)getenv('ELEVENLABS_API_KEY'));
    $voiceId = trim((string)getenv('ELEVENLABS_VOICE_ID'));
    $model = trim((string)(getenv('ELEVENLABS_MODEL') ?: 'eleven_v3'));
    if ($apiKey === '' || $voiceId === '') respond(503, ['error'=>'tts_not_configured']);

    $payload = ['text'=>$text, 'model_id'=>$model];
    $language = trim((string)($data['language'] ?? ''));
    if ($language !== '') $payload['language_code'] = $language;
    $json = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    if ($json === false) respond(400, ['error'=>'invalid_tts_payload']);

    $url = 'https://api.elevenlabs.io/v1/text-to-speech/' . rawurlencode($voiceId)
        . '/stream?output_format=mp3_22050_32';
    $status = 0;
    $errorBody = '';
    $audioStarted = false;
    $ch = curl_init($url);
    if ($ch === false) respond(503, ['error'=>'curl_unavailable']);

    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => $json,
        CURLOPT_HTTPHEADER => [
            'xi-api-key: ' . $apiKey,
            'Content-Type: application/json',
            'Accept: audio/mpeg',
        ],
        CURLOPT_CONNECTTIMEOUT => 10,
        CURLOPT_TIMEOUT => 60,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_RETURNTRANSFER => false,
        CURLOPT_BUFFERSIZE => 4096,
        CURLOPT_HEADERFUNCTION => static function ($curl, string $line) use (&$status): int {
            if (preg_match('#^HTTP/\S+\s+(\d{3})#', trim($line), $match)) $status = (int)$match[1];
            return strlen($line);
        },
        CURLOPT_WRITEFUNCTION => static function ($curl, string $chunk) use (&$status, &$errorBody, &$audioStarted): int {
            if ($status >= 200 && $status < 300) {
                if (!$audioStarted) {
                    while (ob_get_level() > 0) @ob_end_clean();
                    @ini_set('zlib.output_compression', '0');
                    http_response_code(200);
                    header('Content-Type: audio/mpeg');
                    header('Cache-Control: no-store, no-cache, must-revalidate, no-transform');
                    header('X-Accel-Buffering: no');
                    header('X-Content-Type-Options: nosniff');
                    $audioStarted = true;
                }
                echo $chunk;
                flush();
            } elseif (strlen($errorBody) < 65536) {
                $errorBody .= substr($chunk, 0, 65536 - strlen($errorBody));
            }
            return strlen($chunk);
        },
    ]);
    if (defined('CURLOPT_PROTOCOLS') && defined('CURLPROTO_HTTPS')) curl_setopt($ch, CURLOPT_PROTOCOLS, CURLPROTO_HTTPS);

    $ok = curl_exec($ch);
    $curlError = curl_error($ch);
    curl_close($ch);
    if ($audioStarted) exit;
    if ($ok === false) respond(502, ['error'=>'tts_upstream_connection', 'detail'=>$curlError]);
    $safeStatus = ($status >= 400 && $status <= 599) ? $status : 502;
    $detail = trim($errorBody);
    respond($safeStatus, ['error'=>'tts_upstream_error', 'upstream_status'=>$status, 'detail'=>substr($detail, 0, 2048)]);
}

$token = (string)getenv('RELAY_AGENT_TOKEN');
if (strlen($token) < 24 || !hash_equals($token, bearer())) respond(401, ['error'=>'unauthorized']);
$requireHttps = filter_var(getenv('RELAY_REQUIRE_HTTPS') ?: 'true', FILTER_VALIDATE_BOOL);
$trustProxy = filter_var(getenv('RELAY_TRUST_PROXY') ?: 'false', FILTER_VALIDATE_BOOL);
$https = ($_SERVER['HTTPS'] ?? '') === 'on'
    || ($trustProxy && ($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https');
if ($requireHttps && !$https) respond(426, ['error'=>'https_required']);
$root = getenv('RELAY_STORAGE_DIR') ?: sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'agent-windows-relay';
if (!is_dir($root) && !mkdir($root, 0700, true)) respond(500, ['error'=>'storage_unavailable']);
$rootReal = realpath($root);
$publicReal = realpath(__DIR__);
if ($rootReal === false || $publicReal === false
    || $rootReal === $publicReal
    || str_starts_with($rootReal . DIRECTORY_SEPARATOR, $publicReal . DIRECTORY_SEPARATOR)) {
    respond(500, ['error'=>'unsafe_storage']);
}
chmod($rootReal, 0700);

$bucket = $root . DIRECTORY_SEPARATOR . 'rate-' . hash('sha256', $token) . '.json';
$minute = (int)floor(time()/60);
$limit = envInt('RELAY_RATE_LIMIT_PER_MINUTE', 120);
$handle = fopen($bucket, 'c+');
if (!$handle) respond(500, ['error'=>'rate_storage']);
chmod($bucket, 0600);
flock($handle, LOCK_EX);
$saved = json_decode(stream_get_contents($handle) ?: '{}', true);
$count = (($saved['minute'] ?? -1) === $minute) ? (int)($saved['count'] ?? 0) + 1 : 1;
ftruncate($handle, 0); rewind($handle);
fwrite($handle, json_encode(['minute'=>$minute, 'count'=>$count]));
fflush($handle); flock($handle, LOCK_UN); fclose($handle);
if ($count > $limit) respond(429, ['error'=>'rate_limited']);

$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$path = requestPath();
if ($method === 'GET' && $path === '/v1/health') respond(200, ['status'=>'ok']);
if ($method === 'GET' && $path === '/v1/tts/health') {
    $configured = trim((string)getenv('ELEVENLABS_API_KEY')) !== '' && trim((string)getenv('ELEVENLABS_VOICE_ID')) !== '';
    respond($configured && function_exists('curl_init') ? 200 : 503, ['status'=>$configured ? 'configured' : 'not_configured', 'curl'=>function_exists('curl_init')]);
}
if ($method === 'POST' && $path === '/v1/tts/stream') streamElevenLabsTts(readJson());

if ($method === 'POST' && $path === '/v1/audio/sessions') {
    $data = readJson();
    $id = (string)($data['session_id'] ?? bin2hex(random_bytes(16)));
    if (!validSession($id)) respond(400, ['error'=>'invalid_session_id']);
    $allowed = ['ogg_opus'=>'audio/ogg; codecs=opus', 'mp3'=>'audio/mpeg', 'pcm_s16le'=>'audio/L16'];
    $codec = (string)($data['codec'] ?? '');
    $type = (string)($data['content_type'] ?? '');
    if (!isset($allowed[$codec]) || $allowed[$codec] !== $type) respond(415, ['error'=>'unsupported_audio_type']);
    $dir = sessionDir($root, $id);
    if (!is_dir($dir) && !mkdir($dir, 0700, true)) respond(500, ['error'=>'session_storage']);
    chmod($dir, 0700);
    $metadataFile = $dir . DIRECTORY_SEPARATOR . 'metadata.json';
    $metadata = ['session_id'=>$id, 'codec'=>$codec, 'content_type'=>$type,
        'sample_rate'=>(int)($data['sample_rate'] ?? 0), 'channels'=>(int)($data['channels'] ?? 0)];
    if (is_file($metadataFile)) {
        $existing = json_decode((string)file_get_contents($metadataFile), true);
        foreach (['session_id', 'codec', 'content_type', 'sample_rate', 'channels'] as $field) {
            if (!is_array($existing) || ($existing[$field] ?? null) !== $metadata[$field]) {
                respond(409, ['error'=>'session_metadata_conflict']);
            }
        }
    } else {
        $metadata['created_at'] = time();
        if (file_put_contents($metadataFile, json_encode($metadata), LOCK_EX) === false) {
            respond(500, ['error'=>'session_storage']);
        }
        chmod($metadataFile, 0600);
    }
    $received = [];
    foreach (glob($dir . DIRECTORY_SEPARATOR . '*.audio') ?: [] as $file) $received[] = (int)basename($file, '.audio');
    sort($received);
    respond(200, ['session_id'=>$id, 'accepted'=>true, 'received_sequences'=>$received]);
}

if (preg_match('#^/v1/audio/sessions/([^/]+)/chunks/(\d+)$#D', $path, $match) && $method === 'PUT') {
    $id = rawurldecode($match[1]); $sequence = (int)$match[2];
    if (!validSession($id)) respond(400, ['error'=>'invalid_session_id']);
    $dir = sessionDir($root, $id);
    if (!is_file($dir . DIRECTORY_SEPARATOR . 'metadata.json')) respond(404, ['error'=>'session_not_found']);
    $contentType = strtolower(trim(explode(';', $_SERVER['CONTENT_TYPE'] ?? '')[0]));
    if ($contentType !== 'application/octet-stream') respond(415, ['error'=>'invalid_chunk_content_type']);
    $length = (int)($_SERVER['CONTENT_LENGTH'] ?? 0);
    $max = envInt('RELAY_MAX_CHUNK_BYTES', 262144);
    if ($length < 1 || $length > $max) respond(413, ['error'=>'invalid_chunk_size']);
    $expected = strtolower($_SERVER['HTTP_X_CHUNK_SHA256'] ?? '');
    if (!preg_match('/^[a-f0-9]{64}$/D', $expected)) respond(400, ['error'=>'invalid_checksum']);
    $target = $dir . DIRECTORY_SEPARATOR . sprintf('%012d.audio', $sequence);
    $sessionBytes = 0;
    foreach (glob($dir . DIRECTORY_SEPARATOR . '*.audio') ?: [] as $file) {
        if ($file !== $target) $sessionBytes += (int)filesize($file);
    }
    if ($sessionBytes + $length > envInt('RELAY_MAX_SESSION_BYTES', 52428800)) {
        respond(413, ['error'=>'session_too_large']);
    }
    $temp = $target . '.' . bin2hex(random_bytes(6)) . '.tmp';
    $input = fopen('php://input', 'rb'); $output = fopen($temp, 'xb');
    if (!$input || !$output) respond(500, ['error'=>'upload_open']);
    chmod($temp, 0600);
    $hash = hash_init('sha256'); $written = 0;
    while (!feof($input)) {
        $chunk = fread($input, 65536);
        if ($chunk === false) break;
        $written += strlen($chunk);
        if ($written > $max) { fclose($output); unlink($temp); respond(413, ['error'=>'chunk_too_large']); }
        hash_update($hash, $chunk); fwrite($output, $chunk);
    }
    fclose($input); fclose($output);
    $actual = hash_final($hash);
    if (!hash_equals($expected, $actual)) { unlink($temp); respond(422, ['error'=>'checksum_mismatch']); }
    if (is_file($target)) {
        $duplicate = hash_equals(hash_file('sha256', $target), $actual);
        unlink($temp);
        if (!$duplicate) respond(409, ['error'=>'sequence_conflict']);
        respond(200, ['session_id'=>$id, 'sequence'=>$sequence, 'accepted'=>false, 'duplicate'=>true]);
    }
    $exclusive = fopen($target, 'x+b');
    if ($exclusive === false) {
        $duplicate = is_file($target) && hash_equals(hash_file('sha256', $target), $actual);
        unlink($temp);
        if (!$duplicate) respond(409, ['error'=>'sequence_conflict']);
        respond(200, ['session_id'=>$id, 'sequence'=>$sequence, 'accepted'=>false, 'duplicate'=>true]);
    }
    $source = fopen($temp, 'rb');
    if ($source === false || stream_copy_to_stream($source, $exclusive) !== $written) {
        if ($source !== false) fclose($source);
        fclose($exclusive); unlink($target); unlink($temp);
        respond(500, ['error'=>'upload_store']);
    }
    fflush($exclusive); fclose($source); fclose($exclusive); chmod($target, 0600); unlink($temp);
    respond(200, ['session_id'=>$id, 'sequence'=>$sequence, 'accepted'=>true, 'duplicate'=>false]);
}

if (preg_match('#^/v1/audio/sessions/([^/]+)$#D', $path, $match) && $method === 'GET') {
    $id = rawurldecode($match[1]);
    if (!validSession($id)) respond(400, ['error'=>'invalid_session_id']);
    $dir = sessionDir($root, $id);
    if (!is_dir($dir)) respond(404, ['error'=>'session_not_found']);
    $received = [];
    foreach (glob($dir . DIRECTORY_SEPARATOR . '*.audio') ?: [] as $file) $received[] = (int)basename($file, '.audio');
    sort($received);
    respond(200, ['session_id'=>$id, 'received_sequences'=>$received]);
}

if (preg_match('#^/v1/audio/sessions/([^/]+)/finish$#D', $path, $match) && $method === 'POST') {
    $id = rawurldecode($match[1]);
    if (!validSession($id)) respond(400, ['error'=>'invalid_session_id']);
    $dir = sessionDir($root, $id);
    $metadataFile = $dir . DIRECTORY_SEPARATOR . 'metadata.json';
    if (!is_file($metadataFile)) respond(404, ['error'=>'session_not_found']);
    $total = 0;
    foreach (glob($dir . DIRECTORY_SEPARATOR . '*.audio') ?: [] as $file) $total += filesize($file);
    if ($total > envInt('RELAY_MAX_SESSION_BYTES', 52428800)) respond(413, ['error'=>'session_too_large']);
    $forwarder = new StoredOnlyForwarder();
    respond(200, ['session_id'=>$id] + $forwarder->finish($dir, json_decode(file_get_contents($metadataFile), true)));
}

respond(404, ['error'=>'not_found']);
