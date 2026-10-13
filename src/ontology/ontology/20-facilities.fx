// ====================================================================
// Facilities and amenities at a stop point
//
// Two different kinds of statement live here, and the distinction
// matters:
//
//   * An amenity flag (hasWifi) is a slot on the stop point. It comes
//     from a dataset whose *rows are the inventory* -- wifistation has
//     no WiFi column at all, so the existence of a row is the fact.
//
//   * A facility (a platform, a waiting hall) is its own object with a
//     back-reference, because there are several per station and each
//     carries its own attributes.
// ====================================================================

Facility { label: String }.

Platform extends Facility.
PlatformSurface extends Facility.
WaitingHall extends Facility.
Clock extends Facility.
TicketValidator extends Facility.
SectorBoard extends Facility.
Furniture extends Facility.
DepartureBoard extends Facility.
SafetyLine extends Facility.

declare required Facility atStopPoint.

// Every facility points back at its stop point; this gives the inverse
// direction for free, so a station can be asked what it holds.
?S[hasFacility -> ?F] <- ?F:Facility AND ?F[atStopPoint -> ?S].

// --------------------------------------------------------------------
// Derived amenity roll-ups
//
// Presence of at least one facility of a kind, stated about the station
// itself. These are rules rather than ingested flags so that they stay
// consistent with the facility objects they summarise.
// --------------------------------------------------------------------
?S[hasPlatform -> true] <- ?F:Platform AND ?F[atStopPoint -> ?S].
// Planned demolition still describes an existing hall; planned construction
// does not. Unknown lifecycle status cannot establish availability.
?F:ExistingWaitingHall <- ?F:WaitingHall AND ?F[status -> "BESTEHEND"].
?F:ExistingWaitingHall <- ?F:WaitingHall AND ?F[status -> "PROJEKTIERT ABBRUCH"].
?S[hasWaitingHall -> true] <- ?F:ExistingWaitingHall AND ?F[atStopPoint -> ?S].
?S[hasClock -> true] <- ?F:Clock AND ?F[atStopPoint -> ?S].
?S[hasTicketValidator -> true] <- ?F:TicketValidator AND ?F[atStopPoint -> ?S].
?S[hasDepartureBoard -> true] <- ?F:DepartureBoard AND ?F[atStopPoint -> ?S].

// A station offering both a sheltered wait and live departure information.
?S:EquippedStation <- ?S[hasWaitingHall -> true] AND ?S[hasDepartureBoard -> true].

// A long platform, in the sense used by the platform-length dataset.
// The comparison is on a value already present -- FrameX compares, it
// does not compute.
?P:LongPlatform <- ?P:Platform AND ?P[platformLength -> ?L] AND ?L > 320.
