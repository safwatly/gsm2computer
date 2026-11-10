package main

import (
	"encoding/binary"
	"strings"
	"testing"
	"unsafe"
)

func voiceTxID() elemID {
	var id elemID
	setName(&id.Name, "Voice Tx Mute")
	id.Numid = 42
	return id
}

func TestElemValueIsOneIoctlStruct(t *testing.T) {
	if unsafe.Sizeof(elemValue{}) != 1224 {
		t.Fatalf("snd_ctl_elem_value size = %d, want 1224", unsafe.Sizeof(elemValue{}))
	}
	if SNDRV_CTL_IOCTL_ELEM_WRITE != 0xC4C85513 {
		t.Fatalf("ELEM_WRITE ioctl = %#x", SNDRV_CTL_IOCTL_ELEM_WRITE)
	}
}

func TestVoiceTxMuteWritesEachValueOnce(t *testing.T) {
	id := voiceTxID()
	info := elemInfo{ID: id, Type: SND_CTL_ELEM_TYPE_INTEGER, Count: 3}
	raw := []string{"0", "297816064", "0"}
	nums, err := parseControlValues(info, raw, nil)
	if err != nil {
		t.Fatal(err)
	}
	payload, err := elemWritePayload(id, info, nums)
	if err != nil {
		t.Fatal(err)
	}
	if len(payload) != 1224 {
		t.Fatalf("payload len = %d, want one 1224-byte ELEM_WRITE", len(payload))
	}
	// id(64) + indirect(4) + pad(4) = 72. Integers are 8-byte little-endian.
	const base = 72
	got0 := binary.LittleEndian.Uint64(payload[base : base+8])
	got1 := binary.LittleEndian.Uint64(payload[base+8 : base+16])
	got2 := binary.LittleEndian.Uint64(payload[base+16 : base+24])
	if got0 != 0 || got1 != 297816064 || got2 != 0 {
		t.Fatalf("elements = [%d, %d, %d], want [0, 297816064, 0]", got0, got1, got2)
	}
	if got1 == got0 {
		t.Fatal("session id was broadcast from the first value")
	}
	// The rest of the value union stays zero. A second write is not appended.
	for i := base + 24; i < base+1024; i++ {
		if payload[i] != 0 {
			t.Fatalf("value union byte %d = %d, want 0", i, payload[i])
		}
	}
}

func TestSingleIntegerIsBroadcast(t *testing.T) {
	info := elemInfo{Type: SND_CTL_ELEM_TYPE_INTEGER, Count: 3}
	nums, err := parseControlValues(info, []string{"7"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(nums) != 1 {
		t.Fatalf("single value parsed as %d numbers", len(nums))
	}
	buf, err := encodeElemValues(info.Type, info.Count, nums)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		got := binary.LittleEndian.Uint64(buf[i*8 : i*8+8])
		if got != 7 {
			t.Fatalf("element %d = %d, want broadcast 7", i, got)
		}
	}
}

func TestValueCountMustMatch(t *testing.T) {
	info := elemInfo{Type: SND_CTL_ELEM_TYPE_INTEGER, Count: 3}
	_, err := parseControlValues(info, []string{"0", "297816064"}, nil)
	if err == nil || !strings.Contains(err.Error(), "got 2 values") {
		t.Fatalf("err = %v", err)
	}
	_, err = encodeElemValues(info.Type, info.Count, []int64{0, 1, 2, 3})
	if err == nil || !strings.Contains(err.Error(), "got 4 values") {
		t.Fatalf("err = %v", err)
	}
}

func TestEnumValuesAreFourByteSlots(t *testing.T) {
	info := elemInfo{Type: SND_CTL_ELEM_TYPE_ENUMERATED, Count: 2}
	nums, err := parseControlValues(info, []string{"Off", "On"}, func(name string) (uint32, bool) {
		switch strings.ToLower(name) {
		case "off":
			return 0, true
		case "on":
			return 3, true
		default:
			return 0, false
		}
	})
	if err != nil {
		t.Fatal(err)
	}
	buf, err := encodeElemValues(info.Type, info.Count, nums)
	if err != nil {
		t.Fatal(err)
	}
	if binary.LittleEndian.Uint32(buf[0:4]) != 0 || binary.LittleEndian.Uint32(buf[4:8]) != 3 {
		t.Fatalf("enum slots = %d %d", binary.LittleEndian.Uint32(buf[0:4]), binary.LittleEndian.Uint32(buf[4:8]))
	}
	// A broadcast of the first index would have put 0 in the second slot.
	if binary.LittleEndian.Uint32(buf[4:8]) == 0 {
		t.Fatal("second enum was broadcast from the first")
	}
}

func TestBooleanBroadcastStaysOneBit(t *testing.T) {
	info := elemInfo{Type: SND_CTL_ELEM_TYPE_BOOLEAN, Count: 2}
	nums, err := parseControlValues(info, []string{"on"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	buf, err := encodeElemValues(info.Type, info.Count, nums)
	if err != nil {
		t.Fatal(err)
	}
	if binary.LittleEndian.Uint64(buf[0:8]) != 1 || binary.LittleEndian.Uint64(buf[8:16]) != 1 {
		t.Fatal("boolean was not broadcast")
	}
}
