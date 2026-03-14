/**
 * authManager.js
 *
 * Handles OAuth sign-in, sign-out, and auth state for Eat My Seoul.
 *
 * ─── Adding a new provider (Kakao, Naver, etc.) ───────────────────────────────
 *   1. Enable the provider in Supabase Dashboard → Authentication → Providers
 *   2. Set `enabled: true` in the PROVIDERS array below
 *   3. That's it — a button appears automatically in the login modal
 * ─────────────────────────────────────────────────────────────────────────────
 */

import { supabase } from './supabaseClient.js';

// ─── Config ───────────────────────────────────────────────────────────────────

const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
const API_BASE_URL = isLocal ? 'http://localhost:3000' : 'https://eatmyseoul.onrender.com';

// The Kakao REST API key — this is a public key, safe to hardcode in frontend
// (it's visible in the OAuth redirect URL anyway)
const KAKAO_REST_API_KEY = '6928b1e47b4a422ff911f068a1948c44';

// ─── Provider Config ──────────────────────────────────────────────────────────

const PROVIDERS = [
  {
    id: 'google',
    label: 'Continue with Google',
    className: 'auth-btn-google',
    supabaseProvider: 'google',
    enabled: true,
  },
  {
    id: 'kakao',
    label: '카카오로 계속하기',
    className: 'auth-btn-kakao',
    supabaseProvider: 'kakao',
    enabled: true,
    scopes: 'profile_nickname profile_image',
  },
  {
    id: 'naver',
    label: 'Naver로 계속하기',
    className: 'auth-btn-naver',
    supabaseProvider: 'naver',
    enabled: false, // Requires custom OAuth setup in Supabase
  },
];

// ─── State ────────────────────────────────────────────────────────────────────

let _currentUser = null;
const _onChangeCallbacks = [];

export function getUser() { return _currentUser; }

export function onAuthStateChange(callback) {
  _onChangeCallbacks.push(callback);
}

function _notifyChange(user) {
  _onChangeCallbacks.forEach(cb => cb(user));
}

// ─── Modal ────────────────────────────────────────────────────────────────────

function _buildModal() {
  const modal = document.createElement('div');
  modal.id = 'authModal';
  modal.className = 'auth-modal hidden';

  const providerBtns = PROVIDERS
    .filter(p => p.enabled)
    .map(p => `<button class="auth-provider-btn ${p.className}" data-provider="${p.id}">${p.label}</button>`)
    .join('');

  modal.innerHTML = `
    <div class="auth-card">
      <button class="auth-close" id="authClose">×</button>
      <div class="auth-logo">🍜</div>
      <h2 class="auth-title">Save your favourite spots</h2>
      <p class="auth-sub">Sign in to bookmark restaurants and sync them across devices</p>
      <div class="auth-providers">${providerBtns}</div>
    </div>
  `;

  document.body.appendChild(modal);

  modal.addEventListener('click', e => { if (e.target === modal) hideLoginModal(); });
  document.getElementById('authClose').addEventListener('click', hideLoginModal);

  modal.querySelectorAll('.auth-provider-btn').forEach(btn => {
    btn.addEventListener('click', () => signIn(btn.dataset.provider));
  });
}

export function showLoginModal() {
  document.getElementById('authModal')?.classList.remove('hidden');
}

export function hideLoginModal() {
  document.getElementById('authModal')?.classList.add('hidden');
}

// ─── Auth Actions ─────────────────────────────────────────────────────────────

export async function signIn(providerId) {
  if (providerId === 'kakao') {
    _startKakaoLogin();
    return;
  }
  const provider = PROVIDERS.find(p => p.id === providerId);
  if (!provider) return;
  await supabase.auth.signInWithOAuth({
    provider: provider.supabaseProvider,
    options: { redirectTo: window.location.origin },
  });
}

// Redirects the browser to Kakao's auth page (no email scope — avoids KOE205).
function _startKakaoLogin() {
  const redirectUri = window.location.origin + '/';
  const params = new URLSearchParams({
    client_id: KAKAO_REST_API_KEY,
    redirect_uri: redirectUri,
    response_type: 'code',
    scope: 'profile_nickname profile_image openid',
  });
  window.location.href = `https://kauth.kakao.com/oauth/authorize?${params}`;
}

// Called on every page load. If Kakao redirected back with ?code=..., handle it.
async function _handleKakaoRedirect() {
  const urlParams = new URLSearchParams(window.location.search);
  const code = urlParams.get('code');
  if (!code) return;

  // Clean the code from the URL immediately so it doesn't get reused
  const cleanUrl = window.location.pathname;
  window.history.replaceState({}, document.title, cleanUrl);

  try {
    // Exchange the code for an id_token via our backend
    const res = await fetch(`${API_BASE_URL}/auth/kakao/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, redirect_uri: window.location.origin + '/' }),
    });

    const data = await res.json();

    if (!res.ok || !data.id_token) {
      console.error('[Kakao] Backend token exchange failed:', data);
      return;
    }

    // Sign into Supabase using the id_token — bypasses the broken signInWithOAuth
    const { error } = await supabase.auth.signInWithIdToken({
      provider: 'kakao',
      token: data.id_token,
    });

    if (error) {
      console.error('[Kakao] Supabase signInWithIdToken failed:', error);
    }
  } catch (err) {
    console.error('[Kakao] Redirect handling error:', err);
  }
}

export async function signOut() {
  await supabase.auth.signOut();
}

// ─── UI Sync ──────────────────────────────────────────────────────────────────

function _updateAuthButton(user) {
  const btn = document.getElementById('authBtn');
  if (!btn) return;
  if (user) {
    const name = user.user_metadata?.full_name || user.email || '?';
    const initials = name.split(' ').map(w => w[0]).join('').substring(0, 2).toUpperCase();
    btn.textContent = initials;
    btn.classList.add('signed-in');
    btn.title = user.email;
  } else {
    btn.textContent = '👤';
    btn.classList.remove('signed-in');
    btn.title = 'Sign in';
  }
}

function _updateFavBtn(user) {
  const btn = document.getElementById('favBtn');
  if (!btn) return;
  btn.classList.toggle('hidden', !user);
}

// ─── Init ─────────────────────────────────────────────────────────────────────

export function init() {
  _buildModal();
  _handleKakaoRedirect();

  supabase.auth.onAuthStateChange((_event, session) => {
    _currentUser = session?.user ?? null;
    _updateAuthButton(_currentUser);
    _updateFavBtn(_currentUser);
    _notifyChange(_currentUser);
  });

  // Restore existing session on page load
  supabase.auth.getSession().then(({ data: { session } }) => {
    _currentUser = session?.user ?? null;
    _updateAuthButton(_currentUser);
    _updateFavBtn(_currentUser);
    _notifyChange(_currentUser);
  });

  const authBtn = document.getElementById('authBtn');
  if (authBtn) {
    authBtn.addEventListener('click', () => {
      if (_currentUser) {
        if (confirm(`Sign out of ${_currentUser.email}?`)) signOut();
      } else {
        showLoginModal();
      }
    });
  }
}
