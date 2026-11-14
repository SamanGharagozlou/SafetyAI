import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import { demoSessionService } from './services/demoSession'

beforeEach(() => {
  window.history.replaceState(null, '', '/join')
  vi.stubEnv('VITE_DEMO_SESSION_SECONDS', '600')
})

function join(nickname = '') {
  render(<App />)
  fireEvent.change(screen.getByLabelText(/What should I call you/), { target: { value: nickname } })
  fireEvent.click(screen.getByRole('button', { name: /Let's Get Started/ }))
}

describe('visitor onboarding', () => {
  it('opens /join, accepts an optional nickname and labels the simulated session', async () => {
    const user = userEvent.setup()
    render(<App />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Hey there/)
    await user.type(screen.getByLabelText(/What should I call you/), '  Nova  ')
    await user.click(screen.getByRole('button', { name: /Let's Get Started/ }))
    expect(screen.getByRole('heading', { name: 'Talk to ClawMetry' })).toHaveFocus()
    expect(window.location.pathname).toBe('/session')
    expect(screen.getByText('Demo mode', { exact: true })).toBeInTheDocument()
    expect(screen.getByText(/no real robot connection/)).toBeInTheDocument()
    expect(window.location.href).not.toContain('Nova')
    expect(window.history.state).toBeNull()
  })
  it.each(['', '   '])('continues without a name (%j)', nickname => {
    join(nickname)
    expect(screen.getByText('Nice to meet you, Guest!')).toBeInTheDocument()
  })
  it('lets visitors explicitly choose Guest even after typing a nickname', () => {
    render(<App />)
    fireEvent.change(screen.getByLabelText(/What should I call you/), { target: { value: 'Discard me' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue as Guest' }))
    expect(screen.getByText('Nice to meet you, Guest!')).toBeInTheDocument()
  })
  it('clears data on End Session and does not reuse the previous nickname', () => {
    join('Nova')
    fireEvent.click(screen.getByRole('button', { name: 'Leave session' }))
    expect(screen.getByRole('status')).toHaveTextContent('Your demo session has ended')
    expect(screen.getByLabelText(/What should I call you/)).toHaveValue('')
    fireEvent.click(screen.getByRole('button', { name: /Let's Get Started/ }))
    expect(screen.getByText('Nice to meet you, Guest!')).toBeInTheDocument()
  })
  it('announces session creation errors and supports retry', () => {
    vi.spyOn(demoSessionService, 'create').mockImplementationOnce(() => { throw new Error('simulated failure') })
    join('Nova')
    expect(screen.getByRole('alert')).toHaveTextContent('We couldn’t start')
    expect(window.location.pathname).toBe('/join')
    fireEvent.click(screen.getByRole('button', { name: /Let's Get Started/ }))
    expect(screen.getByText('Nice to meet you, Nova!')).toBeInTheDocument()
  })
  it('handles an invalid demo configuration without creating a session', () => {
    vi.stubEnv('VITE_DEMO_SESSION_SECONDS', 'invalid')
    join()
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/join')
  })
})

describe('session expiry', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.stubEnv('VITE_DEMO_SESSION_SECONDS', '2') })
  it('expires, clears the nickname, and allows a new session', () => {
    join('Nova')
    act(() => vi.advanceTimersByTime(1999))
    expect(screen.getByRole('button', { name: 'Leave session' })).toBeInTheDocument()
    act(() => vi.advanceTimersByTime(1))
    expect(screen.getByRole('status')).toHaveTextContent('Your demo session has expired')
    expect(screen.getByLabelText(/What should I call you/)).toHaveValue('')
    fireEvent.click(screen.getByRole('button', { name: /Let's Get Started/ }))
    expect(screen.getByText('Nice to meet you, Guest!')).toBeInTheDocument()
  })
  it('handles a sleeping phone when focus returns after the deadline', () => {
    join('Nova')
    vi.setSystemTime(Date.now() + 3000)
    fireEvent(window, new Event('focus'))
    expect(screen.getByRole('status')).toHaveTextContent('Your demo session has expired')
  })
  it('expires while privacy is open and returns safely to welcome', () => {
    join('Nova')
    fireEvent.click(screen.getByRole('link', { name: 'Privacy information' }))
    act(() => vi.advanceTimersByTime(2000))
    expect(screen.getByRole('heading', { name: 'Privacy, in plain words.' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Back to welcome/ }))
    expect(screen.getByRole('status')).toHaveTextContent('Your demo session has expired')
  })
  it('cancels the previous expiry after a session ends', () => {
    join('Nova')
    fireEvent.click(screen.getByRole('button', { name: 'Leave session' }))
    act(() => vi.advanceTimersByTime(3000))
    expect(screen.getByRole('status')).toHaveTextContent('Your demo session has ended')
  })
})

describe('navigation and refresh behavior', () => {
  it('keeps a session when reading privacy and returning', () => {
    join('Nova')
    fireEvent.click(screen.getByRole('link', { name: 'Privacy information' }))
    fireEvent.click(screen.getByRole('button', { name: /Back to session/ }))
    expect(screen.getByText('Nice to meet you, Nova!')).toBeInTheDocument()
  })
  it('does not restore a session after the app is remounted on /session', () => {
    const view = render(<App />)
    fireEvent.click(screen.getByRole('button', { name: /Let's Get Started/ }))
    view.unmount()
    render(<App />)
    expect(window.location.pathname).toBe('/join')
    expect(screen.getByRole('status')).toHaveTextContent('no active demo session')
  })
  it('ends the session when browser navigation returns to join', () => {
    join('Nova')
    act(() => {
      window.history.replaceState(null, '', '/join')
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    expect(screen.getByRole('status')).toHaveTextContent('Your demo session has ended')
    expect(screen.getByLabelText(/What should I call you/)).toHaveValue('')
  })
  it('opens the root at /join and provides recovery for unknown paths', () => {
    window.history.replaceState(null, '', '/')
    const view = render(<App />)
    expect(window.location.pathname).toBe('/join')
    view.unmount()
    window.history.replaceState(null, '', '/missing')
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Go to welcome' }))
    expect(window.location.pathname).toBe('/join')
  })
})
