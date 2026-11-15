<?php
/*
Plugin Name: Authelia single sign-on
Plugin URI: https://github.com/raphaelcasimir/tinycloud-main-server/tree/main/sso
Description: Opens the admin area without a second login when you already have an Authelia session.
Version: 1.0
*/

if (!defined('YOURLS_ABSPATH')) die();

yourls_add_filter('shunt_is_valid_user', 'sso_authelia_is_valid_user');

// Asks Authelia whether the visitor's session is valid, so a forged header can't sign anyone in.
function sso_authelia_is_valid_user($pre) {
    if ($pre !== yourls_shunt_default()) return $pre;

    $session = $_COOKIE['authelia_session'] ?? '';
    if ($session === '' || preg_match('/[\s;]/', $session)) return $pre;

    $authelia = getenv('SSO_AUTHELIA_URL') ?: 'http://authelia:9091';
    $allowed = array_filter(array_map('trim', explode(',', getenv('SSO_ALLOWED_USERS') ?: '*')));
    $host = preg_replace('/[^A-Za-z0-9.\-:]/', '', $_SERVER['HTTP_HOST'] ?? '');
    $uri = $_SERVER['REQUEST_URI'] ?? '/admin/';

    $context = stream_context_create(['http' => [
        'method' => 'GET',
        'timeout' => 5,
        'ignore_errors' => true,
        'follow_location' => 0,
        'header' => implode("\r\n", [
            'X-Original-URL: https://' . $host . $uri,
            'X-Original-Method: ' . ($_SERVER['REQUEST_METHOD'] ?? 'GET'),
            'X-Forwarded-For: ' . ($_SERVER['HTTP_X_FORWARDED_FOR'] ?? $_SERVER['REMOTE_ADDR'] ?? ''),
            'Cookie: authelia_session=' . $session,
        ]),
    ]]);
    @file_get_contents($authelia . '/api/authz/auth-request', false, $context);
    $headers = function_exists('http_get_last_response_headers')
        ? (http_get_last_response_headers() ?? [])
        : ($http_response_header ?? []);
    if (!$headers || !preg_match('#^HTTP/\S+\s+200#', $headers[0])) return $pre;

    foreach ($headers as $line) {
        if (preg_match('/^remote-user:\s*(\S+)/i', $line, $m) && (in_array('*', $allowed, true) || in_array($m[1], $allowed, true))) {
            yourls_set_user($m[1]);
            return true;
        }
    }
    return $pre;
}
