import abc
from collections.abc import Callable
from typing import ClassVar, Literal, overload

lower16 = 0xFFFF
factor16 = 2**16


def make_recover(index: float, offset: int) -> int:
    return int(index + offset * factor16)


def recover_index(value: int) -> int:
    return int(value & lower16)


def recover_offset(value: int) -> int:
    return int((value - (value & lower16)) / factor16)


DEL_BEFORE = 1
DEL_AFTER = 2
DEL_ACROSS = 4
DEL_SIDE = 8


class MapResult:
    def __init__(self, pos: int, del_info: int = 0, recover: int | None = None) -> None:
        self.pos = pos
        self.del_info = del_info
        self.recover = recover

    #   get deleted() { return (this.delInfo & DEL_SIDE) > 0 }

    #   get deletedBefore() { return (this.delInfo & (DEL_BEFORE | DEL_ACROSS)) > 0 }

    #   get deletedAfter() { return (this.delInfo & (DEL_AFTER | DEL_ACROSS)) > 0 }

    #   get deletedAcross() { return (this.delInfo & DEL_ACROSS) > 0 }

    @property
    def deleted(self) -> bool:
        return (self.del_info & DEL_SIDE) > 0

    @property
    def deleted_before(self) -> bool:
        return (self.del_info & (DEL_BEFORE | DEL_ACROSS)) > 0

    @property
    def deleted_after(self) -> bool:
        return (self.del_info & (DEL_AFTER | DEL_ACROSS)) > 0

    @property
    def deleted_across(self) -> bool:
        return (self.del_info & DEL_ACROSS) > 0


class Mappable(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def map(self, pos: int, assoc: int = 1) -> int: ...

    @abc.abstractmethod
    def map_result(self, pos: int, assoc: int = 1) -> MapResult: ...


class StepMap(Mappable):
    empty: ClassVar["StepMap"]

    def __init__(self, ranges: list[int], inverted: bool = False) -> None:
        # prosemirror-transform overrides the constructor to return the
        # StepMap.empty singleton when ranges are empty.
        # It is not easy to do in Python, and the intent of that is to make sure
        # empty stepmaps can eq to each other, which is already the case in Python.
        self.ranges = ranges
        self.inverted = inverted

    def recover(self, value: int) -> int:
        diff = 0
        index = recover_index(value)
        if not self.inverted:
            for i in range(index):
                diff += self.ranges[i * 3 + 2] - self.ranges[i * 3 + 1]
        return self.ranges[index * 3] + diff + recover_offset(value)

    def map(self, pos: int, assoc: int = 1) -> int:
        return self._map(pos, assoc, True)

    def map_result(self, pos: int, assoc: int = 1) -> MapResult:
        return self._map(pos, assoc, False)

    @overload
    def _map(self, pos: int, assoc: int, simple: Literal[True]) -> int: ...

    @overload
    def _map(self, pos: int, assoc: int, simple: Literal[False]) -> MapResult: ...

    def _map(self, pos: int, assoc: int, simple: bool) -> MapResult | int:
        diff = 0
        old_index = 2 if self.inverted else 1
        new_index = 1 if self.inverted else 2
        for i in range(0, len(self.ranges), 3):
            start = self.ranges[i] - (diff if self.inverted else 0)
            if start > pos:
                break
            old_size = self.ranges[i + old_index]
            new_size = self.ranges[i + new_index]
            end = start + old_size
            if pos <= end:
                if not old_size:
                    side = assoc
                elif pos == start:
                    side = -1
                elif pos == end:
                    side = 1
                else:
                    side = assoc
                result = start + diff + (0 if side < 0 else new_size)
                if simple:
                    return result
                recover = (
                    None
                    if pos == (start if assoc < 0 else end)
                    else make_recover(i / 3, pos - start)
                )
                del_info = (
                    DEL_AFTER
                    if pos == start
                    else (DEL_BEFORE if pos == end else DEL_ACROSS)
                )
                if pos != start if assoc < 0 else pos != end:
                    del_info |= DEL_SIDE
                return MapResult(result, del_info, recover)
            diff += new_size - old_size
        return pos + diff if simple else MapResult(pos + diff, 0, None)

    def touches(self, pos: int, recover: int) -> bool:
        diff = 0
        index = recover_index(recover)
        old_index = 2 if self.inverted else 1
        new_index = 1 if self.inverted else 2
        for i in range(len(self.ranges), 3):
            start = self.ranges[i] - (diff if self.inverted else 0)
            if start > pos:
                break
            old_size = self.ranges[i + old_index]
            end = start + old_size
            if pos <= end and i == index * 3:
                return True
            diff += self.ranges[i + new_index] - old_size
        return False

    def for_each(self, f: Callable[[int, int, int, int], None]) -> None:
        old_index = 2 if self.inverted else 1
        new_index = 1 if self.inverted else 2
        i = 0
        diff = 0
        while i < len(self.ranges):
            start = self.ranges[i]
            old_start = start - (diff if self.inverted else 0)
            new_start = start + (0 if self.inverted else diff)
            old_size = self.ranges[i + old_index]
            new_size = self.ranges[i + new_index]
            f(old_start, old_start + old_size, new_start, new_start + new_size)
            i += 3

    def invert(self) -> "StepMap":
        return StepMap(self.ranges, not self.inverted)

    def __str__(self) -> str:
        return ("-" if self.inverted else "") + str(self.ranges)


StepMap.empty = StepMap([])


class Mapping(Mappable):
    def __init__(
        self,
        maps: list[StepMap] | None = None,
        mirror: list[int] | None = None,
        from_: int | None = None,
        to: int | None = None,
    ) -> None:
        self.maps = maps or []
        self.from_ = from_ or 0
        self.to = len(self.maps) if to is None else to
        self.mirror = mirror

    def slice(self, from_: int = 0, to: int | None = None) -> "Mapping":
        if to is None:
            to = len(self.maps)
        return Mapping(self.maps, self.mirror, from_, to)

    def copy(self) -> "Mapping":
        return Mapping(
            self.maps[:],
            (self.mirror[:] if self.mirror else None),
            self.from_,
            self.to,
        )

    def append_map(self, map: StepMap, mirrors: int | None = None) -> None:
        self.maps.append(map)
        self.to = len(self.maps)
        if mirrors is not None:
            self.set_mirror(len(self.maps) - 1, mirrors)

    def append_mapping(self, mapping: "Mapping") -> None:
        i = 0
        start_size = len(self.maps)
        while i < len(mapping.maps):
            mirr = mapping.get_mirror(i)
            i += 1
            self.append_map(
                mapping.maps[i],
                (start_size + mirr) if (mirr is not None and mirr < i) else None,
            )

    def get_mirror(self, n: int) -> int | None:
        if self.mirror:
            for i in range(len(self.mirror)):
                if (self.mirror[i]) == n:
                    return self.mirror[i + (-1 if i % 2 else 1)]
        return None

    def set_mirror(self, n: int, m: int) -> None:
        if not self.mirror:
            self.mirror = []
        self.mirror.extend([n, m])

    def append_mapping_inverted(self, mapping: "Mapping") -> None:
        i = len(mapping.maps) - 1
        total_size = len(self.maps) + len(mapping.maps)
        while i >= 0:
            mirr = mapping.get_mirror(i)
            self.append_map(
                mapping.maps[i].invert(),
                (total_size - mirr - 1) if (mirr is not None and mirr > i) else None,
            )
            i -= 1

    def invert(self) -> "Mapping":
        inverse = Mapping()
        inverse.append_mapping_inverted(self)
        return inverse

    def map(self, pos: int, assoc: int = 1) -> int:
        if self.mirror:
            return self._map(pos, assoc, True)
        for i in range(self.from_, self.to):
            pos = self.maps[i].map(pos, assoc)
        return pos

    def map_result(self, pos: int, assoc: int = 1) -> MapResult:
        return self._map(pos, assoc, False)

    @overload
    def _map(self, pos: int, assoc: int, simple: Literal[True]) -> int: ...

    @overload
    def _map(self, pos: int, assoc: int, simple: Literal[False]) -> MapResult: ...

    def _map(self, pos: int, assoc: int, simple: bool) -> MapResult | int:
        del_info = 0

        i = self.from_
        while i < self.to:
            map = self.maps[i]
            result = map.map_result(pos, assoc)
            if result.recover is not None:
                corr = self.get_mirror(i)
                if corr is not None and corr > i and corr < self.to:
                    i = corr
                    pos = self.maps[corr].recover(result.recover)
                    i += 1
                    continue
            del_info |= result.del_info
            pos = result.pos
            i += 1
        return pos if simple else MapResult(pos, del_info, None)
