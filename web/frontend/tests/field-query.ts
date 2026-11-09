/**
 * Narrows a label query to the control itself.
 *
 * Every field with help carries an (i) button named "Help for <label>", and
 * getByLabelText matches accessible names as well as labels - so a pattern
 * like /password/i finds the button too.
 */
export const FIELD = { selector: 'input, select, textarea' }
