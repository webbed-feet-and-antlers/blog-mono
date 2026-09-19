import { useCallback, useEffect, useState } from "react";

export function useTheme() {
  const [light, setLight] = useState(
    () => localStorage.getItem("wa-theme") === "light",
  );
  useEffect(() => {
    document.documentElement.classList.toggle("light", light);
    localStorage.setItem("wa-theme", light ? "light" : "dark");
  }, [light]);
  const toggle = useCallback(() => setLight((l) => !l), []);
  return { light, toggle };
}
