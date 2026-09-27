/** Hash links into the app, so every "go to" shares one URL format (see hooks/useRoute.js). */

export const weekHref = weekNo => `#/plan/${weekNo}`;

/** Opens the activity log with that workout's details dialog on top. Takes an activity or its id. */
export const workoutHref = activity => `#/log/${typeof activity === "object" ? activity.activity_id : activity}`;

/** Jump there from code (charts, keyboard handlers) rather than from an <a href>. */
export function jumpTo(href) {
  window.location.hash = href;
}
