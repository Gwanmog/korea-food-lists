/**
 * interactionLogger.js
 *
 * Anonymous interaction logger for the Eat My Seoul map.
 * Collects impression / click / save events and batches them to Supabase.
 * No user accounts required — session_id resets when the tab is closed.
 *
 * Usage:
 *   import { logSearchResults, logPinHover, logPinClick, logSave } from './interactionLogger.js';
 */

// ─── Configuration ────────────────────────────────────────────────────────────

const SUPABASE_URL    = 'https://hnwhfbuoilhmvqkhajzk.supabase.co';
const SUPABASE_ANON_KEY = 'sb_publishable_PToc8Tr7mLRrW3njop25BA_2qhsFME3';
const TABLE           = 'interaction_logs';
const INSERT_ENDPOINT = `${SUPABASE_URL}/rest/v1/${TABLE}`;

// ─── Session Management ───────────────────────────────────────────────────────

/**
 * Returns the current anonymous session ID, creating one if it doesn't exist.
 * Stored in sessionStorage so it resets when the user closes the tab.
 */
function getSessionId() {
  let sessionId = sessionStorage.getItem('ems_session_id');
  if (!sessionId) {
    sessionId = crypto.randomUUID();
    sessionStorage.setItem('ems_session_id', sessionId);
  }
  return sessionId;
}

// ─── Timezone Helpers ─────────────────────────────────────────────────────────

/**
 * Returns an object with the user's local context:
 *   user_local_time  — hour of day (0–23)
 *   day_of_week      — ISO day 1 (Mon) – 7 (Sun)
 *   timezone_offset  — UTC offset in minutes (positive = east of UTC)
 */
function getLocalTimeContext() {
  const now = new Date();
  return {
    user_local_time:  now.getHours(),
    day_of_week:      now.getDay() === 0 ? 7 : now.getDay(), // convert Sun=0 → 7
    timezone_offset:  -now.getTimezoneOffset(),              // JS offset is inverted
  };
}

// ─── Core Insert ──────────────────────────────────────────────────────────────

/**
 * Sends an array of row objects to Supabase in a single batch insert.
 * Uses the anon key — relies on RLS to restrict reads.
 *
 * @param {Object[]} rows
 * @returns {Promise<void>}
 */
async function batchInsert(rows) {
  if (!rows || rows.length === 0) return;

  try {
    const response = await fetch(INSERT_ENDPOINT, {
      method:  'POST',
      headers: {
        'Content-Type':  'application/json',
        'apikey':        SUPABASE_ANON_KEY,
        'Authorization': `Bearer ${SUPABASE_ANON_KEY}`,
        'Prefer':        'return=minimal', // don't return inserted rows (faster)
      },
      body: JSON.stringify(rows),
    });

    if (!response.ok) {
      const error = await response.text();
      console.warn('[interactionLogger] Insert failed:', error);
    }
  } catch (err) {
    console.warn('[interactionLogger] Network error:', err);
  }
}

/**
 * Builds a base row with all shared fields pre-filled.
 *
 * @param {number} vectorId
 * @param {number} interactionType  0 | 1 | 2
 * @param {number} position
 * @param {Object} extras           Optional: { search_term, filter_used }
 * @returns {Object}
 */
function buildRow(vectorId, interactionType, position, extras = {}) {
  return {
    session_id:           getSessionId(),
    interaction_type:     interactionType,
    restaurant_vector_id: vectorId,
    position_in_list:     position,
    search_term:          extras.search_term  ?? null,
    filter_used:          extras.filter_used  ?? null,
    ...getLocalTimeContext(),
  };
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * Negative-sampling batch logger for search results.
 *
 * When the user searches and gets N results, call this once after they click.
 * It sends N rows in one batch:
 *   - N-1 rows with interaction_type 0 (impression / ignored)
 *   -   1 row  with interaction_type 1 (click)
 *
 * @param {number[]} resultsArray   Ordered array of vector IDs shown to the user
 * @param {number}   clickedVectorId  The vector ID the user clicked (or null if none)
 * @param {string}   searchTerm     The query string
 * @param {string}   [filterUsed]   Optional active filter label
 */
export async function logSearchResults(resultsArray, clickedVectorId, searchTerm, filterUsed = null) {
  const extras = { search_term: searchTerm || null, filter_used: filterUsed };

  const rows = resultsArray.map((vectorId, index) => {
    const isClicked = vectorId === clickedVectorId;
    return buildRow(vectorId, isClicked ? 1 : 0, index, extras);
  });

  await batchInsert(rows);
}

/**
 * Log a map-pin hover (impression with no click-through).
 * interaction_type = 0
 *
 * @param {number} vectorId
 */
export async function logPinHover(vectorId) {
  const row = buildRow(vectorId, 0, -1); // position -1 = map pin (not ranked list)
  await batchInsert([row]);
}

/**
 * Log a map-pin click (user opened the detail panel).
 * interaction_type = 1
 *
 * @param {number} vectorId
 * @param {number} [position]  Position in visible list if known, else -1
 */
export async function logPinClick(vectorId, position = -1) {
  const row = buildRow(vectorId, 1, position);
  await batchInsert([row]);
}

/**
 * Log a save / bookmark (highest-intent signal).
 * interaction_type = 2
 *
 * @param {number} vectorId
 * @param {number} [position]
 */
export async function logSave(vectorId, position = -1) {
  const row = buildRow(vectorId, 2, position);
  await batchInsert([row]);
}
