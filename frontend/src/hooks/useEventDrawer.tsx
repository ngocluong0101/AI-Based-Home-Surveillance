import { createContext, useContext } from "react";

/** Opens the event detail drawer; provided by the layout, used by every list of events. */
export const EventDrawerContext = createContext<(eventId: string) => void>(() => undefined);
export const useOpenEvent = () => useContext(EventDrawerContext);
