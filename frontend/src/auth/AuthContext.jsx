import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { onUnauthorized } from "../api/client.js";
import { api } from "../api/endpoints.js";

// The access token lives in sessionStorage: it survives a reload but not closing the tab,
// and is never written to localStorage or cookies by the frontend.
const TOKEN_KEY = "proofstack.access_token";
const EMAIL_KEY = "proofstack.email";

const AuthContext = createContext(null);

function readSession(key) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSession(key, value) {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    // storage unavailable (private mode); the session simply won't survive a reload
  }
}

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => readSession(TOKEN_KEY));
  const [email, setEmail] = useState(() => readSession(EMAIL_KEY));

  const logout = useCallback(() => {
    writeSession(TOKEN_KEY, null);
    writeSession(EMAIL_KEY, null);
    setToken(null);
    setEmail(null);
  }, []);

  useEffect(() => {
    onUnauthorized(logout);
    return () => onUnauthorized(null);
  }, [logout]);

  const login = useCallback(async (userEmail, password) => {
    const result = await api.login(userEmail, password);
    writeSession(TOKEN_KEY, result.access_token);
    writeSession(EMAIL_KEY, userEmail);
    setToken(result.access_token);
    setEmail(userEmail);
  }, []);

  const register = useCallback(
    async (userEmail, password) => {
      await api.register(userEmail, password);
      await login(userEmail, password);
    },
    [login],
  );

  const value = useMemo(
    () => ({ token, email, isAuthenticated: Boolean(token), login, register, logout }),
    [token, email, login, register, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
