import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  CheckboxField,
  Dialog,
  Field,
  FilePicker,
  Input,
  Select,
} from '@/components/ui/primitives'
import { changeLanguage } from '@/i18n'

import { FIELD } from './field-query'

/**
 * Field help lives behind an (i) beside the label. What matters is that it
 * opens for every way people operate this interface - mouse, keyboard and a
 * thumb - closes the way they expect, and never hides the text from a screen
 * reader, which gets it with the field itself.
 */

// jsdom has no PointerEvent, and without one the pointer type a test passes
// is dropped - which is the one thing the hover logic reads.
if (typeof window.PointerEvent === 'undefined') {
  class PointerEventStub extends MouseEvent {
    pointerType: string
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init)
      this.pointerType = init.pointerType ?? 'mouse'
    }
  }
  window.PointerEvent = PointerEventStub as unknown as typeof PointerEvent
}

beforeEach(async () => {
  await changeLanguage('en')
})

function field(props: Partial<Parameters<typeof Field>[0]> = {}) {
  render(
    <>
      <Field label="Network name" hint="The SSID the probe signs in to." {...props}>
        <Input />
      </Field>
      <button type="button">elsewhere</button>
    </>,
  )
  return screen.getByRole('button', { name: 'Help for Network name' })
}

describe('the help popover', () => {
  it('opens on keyboard focus and closes on Escape, keeping the focus', async () => {
    const user = userEvent.setup()
    const info = field()

    // The (i) sits in the label row, so it comes before its control.
    await user.tab()
    expect(info).toHaveFocus()
    expect(info).toHaveAttribute('aria-expanded', 'true')
    const panel = document.getElementById(info.getAttribute('aria-controls') ?? '')
    expect(panel).toHaveTextContent('The SSID the probe signs in to.')

    await user.keyboard('{Escape}')
    expect(info).toHaveAttribute('aria-expanded', 'false')
    expect(info).toHaveFocus()
  })

  it('closes when the focus moves on', async () => {
    const user = userEvent.setup()
    const info = field()
    await user.tab()
    expect(info).toHaveAttribute('aria-expanded', 'true')
    await user.tab() // on to the input
    expect(info).toHaveAttribute('aria-expanded', 'false')
  })

  it('toggles on a tap and closes on a press outside', async () => {
    const info = field()

    // A tap: touch pointer, no hover, then the click.
    fireEvent.pointerOver(info, { pointerType: 'touch' })
    fireEvent.pointerDown(info, { pointerType: 'touch' })
    fireEvent.click(info)
    expect(info).toHaveAttribute('aria-expanded', 'true')

    fireEvent.pointerDown(info, { pointerType: 'touch' })
    fireEvent.click(info)
    expect(info).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(info)
    expect(info).toHaveAttribute('aria-expanded', 'true')
    fireEvent.pointerDown(screen.getByRole('button', { name: 'elsewhere' }))
    expect(info).toHaveAttribute('aria-expanded', 'false')
  })

  describe('with a mouse', () => {
    beforeEach(() => {
      vi.useFakeTimers()
    })
    afterEach(() => {
      vi.useRealTimers()
    })

    it('opens on hover after a short delay and closes on leaving', () => {
      const info = field()

      fireEvent.pointerOver(info, { pointerType: 'mouse' })
      expect(info).toHaveAttribute('aria-expanded', 'false')
      act(() => {
        vi.advanceTimersByTime(300)
      })
      expect(info).toHaveAttribute('aria-expanded', 'true')

      fireEvent.pointerOut(info, { pointerType: 'mouse' })
      act(() => {
        vi.advanceTimersByTime(200)
      })
      expect(info).toHaveAttribute('aria-expanded', 'false')
    })

    it('stays open after a click, even when the pointer leaves', () => {
      const info = field()
      fireEvent.pointerOver(info, { pointerType: 'mouse' })
      fireEvent.click(info)
      fireEvent.pointerOut(info, { pointerType: 'mouse' })
      act(() => {
        vi.advanceTimersByTime(500)
      })
      expect(info).toHaveAttribute('aria-expanded', 'true')
    })
  })

  it('closes on the first Escape inside a dialog, and the dialog on the second', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(
      <Dialog title="New variant" onClose={onClose}>
        <Field label="Network name" hint="The SSID the probe signs in to.">
          <Input />
        </Field>
      </Dialog>,
    )
    const info = screen.getByRole('button', { name: 'Help for Network name' })
    await user.click(info)
    expect(info).toHaveAttribute('aria-expanded', 'true')

    await user.keyboard('{Escape}')
    expect(info).toHaveAttribute('aria-expanded', 'false')
    expect(onClose).not.toHaveBeenCalled()

    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

describe('the field primitive', () => {
  it('names the control by its label and describes it by its help, unopened', () => {
    field()
    const input = screen.getByRole('textbox', { name: 'Network name' })
    expect(input).toHaveAccessibleDescription('The SSID the probe signs in to.')
    // No paragraph under the control: the only visible text is the label.
    expect(screen.getByRole('button', { name: 'Help for Network name' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
  })

  it('names a select by its label, not by its first option', () => {
    render(
      <Field label="Sign-in method">
        <Select>
          <option value="">—</option>
          <option value="psk">PSK</option>
        </Select>
      </Field>,
    )
    expect(screen.getByRole('combobox', { name: 'Sign-in method' })).toBeInTheDocument()
  })

  it('keeps facts about the value below the control, an error before them', () => {
    const { rerender } = render(
      <Field label="Password" hint="The password for PEAP." note="Stored. Leave empty to keep it.">
        <Input />
      </Field>,
    )
    const input = screen.getByLabelText('Password', FIELD)
    expect(screen.getByText('Stored. Leave empty to keep it.')).toBeVisible()
    expect(input).toHaveAccessibleDescription(
      'The password for PEAP. Stored. Leave empty to keep it.',
    )

    rerender(
      <Field label="Password" note="Stored. Leave empty to keep it." error="Too short.">
        <Input />
      </Field>,
    )
    expect(screen.queryByText('Stored. Leave empty to keep it.')).not.toBeInTheDocument()
    expect(input).toHaveAccessibleDescription('Too short.')
    expect(input).toHaveAttribute('aria-invalid', 'true')
  })

  it('marks the exception: optional, never required', () => {
    render(
      <>
        <Field label="Display name" optional>
          <Input />
        </Field>
        <Field label="User name">
          <Input />
        </Field>
      </>,
    )
    expect(screen.getAllByText('optional')).toHaveLength(1)
    expect(screen.queryByText('*')).not.toBeInTheDocument()
  })

  it('shows the parameter name muted beside the label', () => {
    render(
      <Field label="Server domain" technicalName="domain_suffix_match">
        <Input />
      </Field>,
    )
    expect(screen.getByRole('textbox', { name: 'Server domain' })).toBeInTheDocument()
    expect(screen.getByText('domain_suffix_match')).toBeInTheDocument()
  })

  it('gives the file picker and the checkbox the same help', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <>
        <FilePicker
          label="CA certificate"
          hint="The authority that signed the server."
          prompt="Choose file"
          onSelect={() => {}}
        />
        <CheckboxField
          label="Skip server check"
          hint="Signs in without checking."
          checked={false}
          onChange={onChange}
        />
      </>,
    )
    expect(screen.getByLabelText('CA certificate', FIELD)).toHaveAccessibleDescription(
      'The authority that signed the server.',
    )
    const box = screen.getByRole('checkbox', { name: 'Skip server check' })
    expect(box).toHaveAccessibleDescription('Signs in without checking.')

    // The (i) sits outside the label: pressing it must not tick the box.
    await user.click(screen.getByRole('button', { name: 'Help for Skip server check' }))
    expect(onChange).not.toHaveBeenCalled()
    expect(box).not.toBeChecked()
  })
})
