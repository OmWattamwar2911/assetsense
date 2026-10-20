import { useEffect, useRef, useState } from 'react'
import { ticker } from '@/data/mockData'
import type { SearchAsset } from '@/types'
import { SearchDropdown } from './SearchDropdown'
import { Ticker } from './Ticker'
import { BrandMark, SearchIcon } from './ui'

export function TopBar({ onSelectAsset }: { onSelectAsset: (asset: SearchAsset) => void }) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const areaRef = useRef<HTMLDivElement>(null)

  // Close on outside click or Escape.
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (areaRef.current && !areaRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [])

  return (
    <header className="topbar">
      <div className="topbar-main">
        <div className="brand">
          <BrandMark />
          <span>AssetSense</span>
        </div>

        <div className="search-area" ref={areaRef}>
          <label className={`search ${open ? 'open' : ''}`}>
            <SearchIcon />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onFocus={() => setOpen(true)}
              placeholder="Search stocks, crypto, funds, gold"
              aria-label="Search assets"
              aria-expanded={open}
            />
            <kbd>⌘ K</kbd>
          </label>
          {open && (
            <SearchDropdown
              query={query}
              onRetry={() => setQuery('')}
              onSelect={(asset) => {
                onSelectAsset(asset)
                setQuery('')
                setOpen(false)
              }}
            />
          )}
        </div>

        <div className="market-status">
          <span className="status-dot" />
          MARKET OPEN
          <span className="num">3:42:18</span>
        </div>
      </div>
      <Ticker items={ticker} />
    </header>
  )
}
