/**
 * favoritesManager.js
 *
 * Manages the current user's saved restaurants.
 * Uses an in-memory Set for instant reads, synced to Supabase for persistence.
 */

import { supabase } from './supabaseClient.js';
import { logSave } from './interactionLogger.js';

const TABLE = 'user_favorites';

let _favorites = new Set(); // Set of vector_ids (as Numbers)

// ─── Read ─────────────────────────────────────────────────────────────────────

export function isFavorited(vectorId) {
  return _favorites.has(Number(vectorId));
}

export function getAll() {
  return _favorites;
}

// ─── Lifecycle ────────────────────────────────────────────────────────────────

export async function load(userId) {
  _favorites.clear();
  const { data, error } = await supabase
    .from(TABLE)
    .select('restaurant_vector_id')
    .eq('user_id', userId);

  if (error) {
    console.warn('[favorites] Load failed:', error.message);
    return;
  }

  data.forEach(row => _favorites.add(Number(row.restaurant_vector_id)));
}

export function clear() {
  _favorites.clear();
}

// ─── Toggle ───────────────────────────────────────────────────────────────────

/**
 * Saves or unsaves a restaurant for the current user.
 * @returns {boolean} true if now saved, false if now unsaved
 */
export async function toggleFavorite(userId, vectorId) {
  const id = Number(vectorId);

  if (_favorites.has(id)) {
    const { error } = await supabase
      .from(TABLE)
      .delete()
      .eq('user_id', userId)
      .eq('restaurant_vector_id', id);

    if (!error) _favorites.delete(id);
    return false;
  } else {
    const { error } = await supabase
      .from(TABLE)
      .insert({ user_id: userId, restaurant_vector_id: id });

    if (!error) {
      _favorites.add(id);
      logSave(id); // record as high-intent signal for DeepFM
    }
    return true;
  }
}
