import { NavLink, Route, Routes } from 'react-router-dom'
import AccidentDetailPage from './pages/AccidentDetailPage'
import AccidentsPage from './pages/AccidentsPage'
import DashboardPage from './pages/DashboardPage'
import { TEXT } from './ui/textZh'

function App() {
  return (
    <div className="app">
      <div className="bg" aria-hidden="true" />
      <header className="topbar">
        <div className="brand">
          <div className="brandMark" aria-hidden="true" />
          <div className="brandText">
            <div className="brandTitle">{TEXT.app.brandTitle}</div>
            <div className="brandSub">{TEXT.app.brandSub}</div>
          </div>
        </div>
        <nav className="nav">
          <NavLink to="/" end className={({ isActive }) => (isActive ? 'navItem active' : 'navItem')}>
            {TEXT.app.navDashboard}
          </NavLink>
          <NavLink to="/accidents" className={({ isActive }) => (isActive ? 'navItem active' : 'navItem')}>
            {TEXT.app.navAccidents}
          </NavLink>
        </nav>
      </header>

      <main className="main">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/accidents" element={<AccidentsPage />} />
          <Route path="/accidents/:id" element={<AccidentDetailPage />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
